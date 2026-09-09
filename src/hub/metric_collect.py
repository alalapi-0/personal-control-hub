"""Registry-dispatched, read-only collection; persistence lives in the Hub ledger."""
from __future__ import annotations

from pathlib import Path
from importlib import import_module

from hub.connection_records import RecordError, content_hash, identifier, require
from hub.connection_sources import SourceResolver, _root_path_allowed
from hub.connections import select_value
from hub.metric_sources import read_structured
from hub.metrics import VERSION, issue, metric, utcnow, validate_metric

DOMAIN_ADAPTERS = {
    "manga": ("hub.metric_manga", "collect_manga"),
    "wechat": ("hub.metric_wechat", "collect_wechat"),
    "anime": ("hub.metric_catalogs", "collect_anime"),
    "pixel": ("hub.metric_catalogs", "collect_pixel"),
    "study": ("hub.metric_documents", "collect_study"),
    "story": ("hub.metric_documents", "collect_story"),
    "zarathustra": ("hub.metric_documents", "collect_zarathustra"),
    "cognitive": ("hub.metric_documents", "collect_cognitive"),
    "music": ("hub.metric_music", "collect_music"),
    "continuation": ("hub.metric_continuation", "collect_continuation"),
    "validation_runs": ("hub.metric_validation", "collect_validation_runs"),
    "downloader": ("hub.metric_saved_tools", "collect_downloader"),
    "workspace_checks": ("hub.metric_saved_tools", "collect_workspace_checks"),
}


def collect_declared(root, project_id, observed_at, sources):
    """Explicit numeric selectors, not arbitrary code or governance completion."""
    rows, issues, versions = [], [], []
    for source in sources:
        path = source["path"]
        try:
            data, meta = read_structured(root, path)
            versions.append(meta["sha256"])
        except (OSError, RecordError, ValueError):
            data, meta = None, None
            issues.append(issue(project_id, "missing_or_invalid_source", path,
                                recovery_condition="Restore the declared metadata source; collection never runs its producer."))
        for spec in source["metrics"]:
            value, reason = None, "Declared source unavailable."
            business_at = None
            if data is not None:
                try:
                    raw = select_value(data, {"path": spec["selector"]})
                    op = spec.get("operation", "number")
                    if op == "length":
                        require(type(raw) in (list, dict), "count source must be a collection")
                        value = len(raw)
                    elif op == "bool":
                        require(type(raw) is bool, "boolean source required")
                        value = int(raw)
                    elif op == "number":
                        require(type(raw) in (int, float), "numeric source required")
                        value = raw
                    else:
                        raise RecordError("unsupported count operation")
                    reason = None
                except (RecordError, KeyError, IndexError, TypeError):
                    reason = "Declared numeric field unavailable or invalid."
                if source.get("business_time"):
                    try:
                        candidate = select_value(data, {"path": source["business_time"]})
                        from hub.connection_records import timestamp
                        timestamp(candidate, "business time")
                        business_at = candidate
                    except (RecordError, KeyError, IndexError, TypeError):
                        issues.append(issue(project_id, "invalid_business_time", path))
            version = content_hash([spec, value, reason, business_at])
            try:
                row = metric(project_id, spec["id"], value, spec["unit"], path + "#" + "/".join(map(str, spec["selector"])),
                    version, observed_at, dimensions=spec.get("dimensions"), business_at=business_at,
                    reason=reason, counting_basis=spec["counting_basis"])
            except RecordError:
                row = metric(project_id, spec["id"], None, spec["unit"], path, version, observed_at,
                    dimensions=spec.get("dimensions"), quality="invalid", reason="Numeric field failed contract validation.",
                    counting_basis=spec["counting_basis"])
            rows.append(row)
    return dict(metrics=rows, issues=issues, disposition="resolved" if rows and all(r["quality"] == "good" for r in rows) else "partial",
                source_version=content_hash(versions))


class MetricCollector:
    def __init__(self, root, *, clock=utcnow, remote_git=False, github_ci=False):
        self.resolver = SourceResolver(Path(root))
        self.registry = self.resolver.registry
        self.projects = self.resolver.projects
        self.clock = clock
        require(type(remote_git) is bool and type(github_ci) is bool, "remote options must be boolean")
        self.remote_git, self.github_ci = remote_git, github_ci
        path = Path(root) / "data/connections/metric_sources.yaml"
        self.config, _ = read_structured(Path(root), str(path.relative_to(root)))
        self.validate_config()
        # Input identity includes implementation, rather than relying only on a manually bumped version.
        modules = sorted(Path(__file__).parent.glob("metric*.py"))
        code = content_hash({p.name: content_hash(p.read_text()) for p in modules})
        self.identity = content_hash([self.resolver.authority, self.config, VERSION, code,
                                      {"remote_git": remote_git, "github_ci": github_ci}])

    def validate_config(self):
        require(self.config.get("schema_version") == VERSION and type(self.config.get("projects")) is dict, "invalid metric source configuration")
        require(set(self.config["projects"]) <= set(self.projects), "metric config contains unknown projects")
        for pid, spec in self.config["projects"].items():
            require(type(spec) is dict, "project source declaration must be an object")
            require(spec.get("adapter") in {"declared", "novel", "unmapped"} | set(DOMAIN_ADAPTERS), "unsupported domain adapter")
            for key in ("metadata_root", "editorial_root", "data_root"):
                require(key not in spec or type(spec[key]) is str and Path(spec[key]).is_absolute(),
                        "external metadata root must be an explicit absolute path")
            for source in spec.get("sources", []):
                require(type(source["path"]) is str and type(source["metrics"]) is list, "invalid metric source")
                for m in source["metrics"]:
                    require(type(m["selector"]) is list and m.get("counting_basis") and m.get("unit"), "metric selector/basis/unit required")
                    require(m.get("operation", "number") in {"number", "length", "bool"}, "invalid numeric operation")
            reports = spec.get("validation_reports", [])
            require(type(reports) is list, "validation reports must be a list")
            require(spec["adapter"] != "validation_runs" or bool(reports), "functional run reports required")
            report_ids = []
            for report in reports:
                require(type(report) is dict and type(report.get("path")) is str, "invalid validation report")
                identifier(report.get("id"), "validation report id")
                require(report.get("format") in {"feature_report", "gate_arrays", "universal_player_guard_result_v1"}, "unsupported validation format")
                if report.get("format") == "universal_player_guard_result_v1":
                    require(type(report.get("suite")) is str and report["suite"] in {"core", "vlckit", "media", "raw"}, "guard suite required")
                require("root" not in report or type(report["root"]) is str and Path(report["root"]).is_absolute(), "invalid report root")
                report_ids.append(report["id"])
            require(len(report_ids) == len(set(report_ids)), "duplicate validation report ids")

    def collect(self, project_id):
        project = self.projects[project_id]
        observed = self.clock()
        result = dict(project_id=project_id, observed_at=observed, disposition="partial", metrics=[], issues=[], source_versions={},
                      registry_binding=content_hash(project), collector_identity=self.identity)
        if project.get("local_presence", {}).get("status") == "removed_local" or project.get("current_state_status") == "removed_local":
            result["disposition"] = "removed_local"
            return result
        if project.get("connection_read_allowed", project.get("enabled")) is not True or project.get("access_profile") == "no_current_goal_access":
            result["disposition"] = "disabled"
            return result
        try:
            self.resolver._check_registry()
            registered = Path(project["root_path"]).expanduser()
            _root_path_allowed(registered)
            root = registered.resolve(strict=True)
            _root_path_allowed(root)
            require(root.is_dir(), "root not a directory")
        except (OSError, RecordError):
            result["disposition"] = "offline"
            result["issues"] = [issue(project_id, "root_unavailable", "registry:root_path")]
            return result
        from hub.metric_git import collect_git
        groups = [("git", lambda: collect_git(root, project_id, observed))]
        spec = self.config["projects"].get(project_id, {"adapter": "unmapped"})
        if self.remote_git or self.github_ci:
            from hub.metric_remote import collect_remote
            groups.append(("remote", lambda: collect_remote(root, project_id, observed, spec,
                remote_git=self.remote_git, github_ci=self.github_ci)))
        if spec["adapter"] == "novel":
            from hub.metric_novel import collect_novel
            groups.append(("business", lambda: collect_novel(root, project_id, observed)))
        elif spec["adapter"] in DOMAIN_ADAPTERS:
            module_name, function_name = DOMAIN_ADAPTERS[spec["adapter"]]
            domain_function = getattr(import_module(module_name), function_name)
            groups.append(("business", lambda: domain_function(root, project_id, observed, spec)))
        elif spec["adapter"] == "declared":
            groups.append(("business", lambda: collect_declared(root, project_id, observed, spec["sources"])))
        else:
            result["issues"].append(issue(project_id, "missing_business_mapping", "registry:" + project_id,
                recovery_condition="Bind actual domain/run metadata; governance complete is not business progress."))
        if spec.get("validation_reports") and spec["adapter"] != "validation_runs":
            from hub.metric_validation import collect_validation
            groups.append(("validation", lambda: collect_validation(root, project_id, observed, spec["validation_reports"])))
        for name, function in groups:
            try:
                group = function()
                for row in group["metrics"]:
                    try:
                        result["metrics"].append(validate_metric(row))
                    except RecordError:
                        result["issues"].append(issue(project_id, "invalid_metric", name + ":" + str(row.get("metric_id"))))
                result["issues"].extend(group["issues"])
                result["source_versions"][name] = group["source_version"]
                if name == "business":
                    result["disposition"] = group["disposition"]
                elif name == "validation" and group["disposition"] != "resolved":
                    result["disposition"] = "partial"
                elif group["disposition"] == "no_git":
                    result["issues"].append(issue(project_id, "no_git", "registry:root_path"))
            except (OSError, RecordError, ValueError, KeyError, TypeError) as exc:
                result["issues"].append(issue(project_id, "collector_failure", name,
                    recovery_condition="Inspect source schema and collector; no workflow was executed."))
        self.resolver._check_registry()
        require(registered.resolve(strict=True) == root, "root binding changed during collection")
        if result["issues"] and result["disposition"] == "resolved":
            result["disposition"] = "partial"
        return result

    def refresh(self, store, request_id, project_ids=None):
        ids = project_ids or list(self.projects)
        require(len(ids) == len(set(ids)) and set(ids) <= set(self.projects), "unknown or duplicate projects")
        store.begin(request_id, ids, self.identity)
        receipts = []
        for pid in ids:
            previous = store.receipt(request_id, pid)
            receipts.append(previous if previous else store.save(request_id, self.collect(pid)))
        return receipts
