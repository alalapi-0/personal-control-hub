"""Compose native Figma exports into comparable color-selection sheets."""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

UNIT = Path(__file__).resolve().parent
PALETTES = json.loads((UNIT / 'palettes.json').read_text())['palettes']
FONT = '/System/Library/Fonts/STHeiti Medium.ttc'
def font(size):
    return ImageFont.truetype(FONT, size)
def fit(path, size):
    im = Image.open(path).convert('RGB')
    im.thumbnail(size, Image.Resampling.LANCZOS)
    return im

contact = Image.new('RGB', (2528, 1080), '#E8E8ED')
cd = ImageDraw.Draw(contact)
for i, p in enumerate(PALETTES):
    r = p['roles']; key = p['id']; x = 24 + i * 500
    cd.rounded_rectangle((x, 24, x + 480, 1056), 16, fill=r['canvas'])
    cd.text((x + 20, 46), f"{i + 1:02d}  {p['name']}", font=font(29), fill=r['text'])
    for j, role in enumerate(['surface', 'text', 'accent', 'warning', 'success']):
        cd.rounded_rectangle((x + 20 + j * 88, 99, x + 94 + j * 88, 115), 4, fill=r[role])
    desktop = UNIT / f'previews/{key}--overview--desktop.png'
    mobile = UNIT / f'previews/{key}--overview--mobile.png'
    contact.paste(fit(desktop, (440, 275)), (x + 20, 148))
    phone = fit(mobile, (250, 542)); contact.paste(phone, (x + 115, 454))
    cd.text((x + 20, 1017), 'C 排版 · 相同示例数据', font=font(18), fill=r['muted'])
    single = Image.new('RGB', (1600, 1080), r['canvas']); sd = ImageDraw.Draw(single)
    sd.text((40, 35), f"{i + 1:02d}  {p['name']}", font=font(50), fill=r['text'])
    sd.text((40, 104), p['mood'], font=font(25), fill=r['muted'])
    single.paste(fit(desktop, (1152, 720)), (40, 178))
    single.paste(fit(mobile, (312, 676)), (1240, 178))
    for j, role in enumerate(['canvas', 'surface', 'text', 'accent', 'warning', 'success']):
        sx = 40 + j * 255
        sd.rounded_rectangle((sx, 940, sx + 223, 985), 8, fill=r[role], outline=r['border'], width=1)
        sd.text((sx, 1000), f'{role}  {r[role]}', font=font(18), fill=r['text'])
    single.save(UNIT / f'previews/{key}--comparison.png')
contact.save(UNIT / 'previews/five-palettes.png')
print('Created five individual sheets and one same-order contact sheet.')
