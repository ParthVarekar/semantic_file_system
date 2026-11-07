"""Hand-drawn (Pillow) scenes with recognizable content for text-to-image evaluation."""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw

from lfi.synthetic import _font


def grad(im, top, bottom, y0=0, y1=None):
    d = ImageDraw.Draw(im)
    y1 = y1 or im.size[1]
    for y in range(y0, y1):
        t = (y - y0) / max(1, y1 - y0)
        d.line([(0, y), (im.size[0], y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))


def beach():
    im = Image.new("RGB", (1200, 800))
    grad(im, (90, 160, 235), (180, 220, 250), 0, 380)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 380, 1200, 560], fill=(20, 120, 170))
    for y in range(400, 560, 25):
        for x in range(0, 1200, 80):
            d.arc([x, y, x + 60, y + 14], 180, 360, fill=(220, 240, 255), width=2)
    d.polygon([(0, 560), (1200, 530), (1200, 800), (0, 800)], fill=(236, 214, 160))
    d.ellipse([960, 60, 1080, 180], fill=(255, 230, 90))
    d.line([(250, 780), (275, 560)], fill=(90, 70, 50), width=6)  # umbrella
    d.pieslice([140, 490, 410, 640], 180, 360, fill=(230, 40, 40))
    d.rectangle([800, 690, 950, 720], fill=(60, 120, 220))  # towel
    d.line([(1080, 790), (1050, 470)], fill=(110, 80, 50), width=18)  # palm
    for a in range(0, 360, 50):
        d.line([(1050, 470), (1050 + 160 * math.cos(math.radians(a)), 470 + 70 * math.sin(math.radians(a)))],
               fill=(30, 140, 50), width=14)
    return im


def mountains():
    im = Image.new("RGB", (1200, 800))
    grad(im, (70, 130, 220), (200, 225, 250), 0, 600)
    d = ImageDraw.Draw(im)
    for cx, h, w in [(250, 430, 380), (620, 520, 450), (980, 400, 360)]:
        base = 650
        d.polygon([(cx - w, base), (cx, base - h), (cx + w, base)], fill=(110, 110, 125))
        d.polygon([(cx - w * 0.28, base - h * 0.72), (cx, base - h), (cx + w * 0.28, base - h * 0.72),
                   (cx + w * 0.1, base - h * 0.66), (cx - w * 0.08, base - h * 0.7)], fill=(250, 250, 255))
    d.rectangle([0, 650, 1200, 800], fill=(235, 240, 248))
    return im


def forest():
    rnd = random.Random(11)
    im = Image.new("RGB", (1200, 800), (170, 210, 235))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 600, 1200, 800], fill=(60, 110, 40))
    for _ in range(45):
        x, base = rnd.randint(-40, 1240), rnd.randint(560, 790)
        h, w = rnd.randint(220, 420), rnd.randint(70, 130)
        d.rectangle([x - 10, base - 30, x + 10, base + 20], fill=(90, 60, 30))
        g = (rnd.randint(10, 40), rnd.randint(90, 140), rnd.randint(30, 60))
        for k in range(3):
            top = base - h + k * h // 4
            d.polygon([(x - w + k * 8, top + h // 2), (x, top), (x + w - k * 8, top + h // 2)], fill=g)
    return im


def skyline():
    rnd = random.Random(12)
    im = Image.new("RGB", (1200, 800))
    grad(im, (5, 10, 40), (40, 30, 90))
    d = ImageDraw.Draw(im)
    x = 0
    while x < 1200:
        w, h = rnd.randint(60, 140), rnd.randint(200, 620)
        d.rectangle([x, 800 - h, x + w, 800], fill=(20, 20, 30))
        for wy in range(800 - h + 15, 790, 26):
            for wx in range(x + 8, x + w - 12, 18):
                if rnd.random() < 0.55:
                    d.rectangle([wx, wy, wx + 9, wy + 13], fill=(255, 220, 120))
        x += w + rnd.randint(4, 20)
    return im


def cake():
    im = Image.new("RGB", (1000, 800), (250, 235, 240))
    d = ImageDraw.Draw(im)
    d.ellipse([180, 620, 820, 720], fill=(240, 240, 240))
    d.rectangle([250, 440, 750, 660], fill=(245, 170, 200))
    d.ellipse([250, 410, 750, 470], fill=(255, 200, 220))
    d.rectangle([310, 300, 690, 450], fill=(255, 245, 230))
    d.ellipse([310, 275, 690, 325], fill=(255, 250, 240))
    for i, x in enumerate(range(350, 680, 55)):
        d.rectangle([x, 200, x + 12, 290], fill=[(80, 160, 240), (240, 200, 60), (120, 200, 120)][i % 3])
        d.ellipse([x - 6, 160, x + 18, 205], fill=(255, 160, 30))
    for x in range(270, 740, 40):
        d.ellipse([x, 530, x + 18, 548], fill=(200, 40, 90))
    d.text((300, 40), "Happy Birthday!", font=_font(64), fill=(200, 40, 120))
    return im


def house():
    im = Image.new("RGB", (1000, 800), (150, 210, 250))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 620, 1000, 800], fill=(90, 190, 70))
    d.rectangle([300, 380, 700, 650], fill=(240, 200, 120), outline="black", width=5)
    d.polygon([(260, 390), (500, 200), (740, 390)], fill=(190, 50, 40), outline="black")
    d.rectangle([460, 500, 550, 650], fill=(120, 70, 30), outline="black", width=4)
    for x in (340, 600):
        d.rectangle([x, 430, x + 70, 500], fill=(180, 230, 255), outline="black", width=4)
        d.line([(x + 35, 430), (x + 35, 500)], fill="black", width=3)
        d.line([(x, 465), (x + 70, 465)], fill="black", width=3)
    d.ellipse([80, 60, 200, 180], fill=(255, 220, 0))
    d.rectangle([600, 230, 640, 320], fill=(150, 80, 60), outline="black", width=3)
    return im


def sailboat():
    im = Image.new("RGB", (1200, 800))
    grad(im, (120, 180, 240), (210, 230, 250), 0, 450)
    d = ImageDraw.Draw(im)
    grad_sea = Image.new("RGB", (1200, 350))
    grad(grad_sea, (20, 90, 160), (10, 50, 110))
    im.paste(grad_sea, (0, 450))
    d = ImageDraw.Draw(im)
    for y in range(470, 800, 30):
        for x in range(0, 1200, 90):
            d.arc([x, y, x + 70, y + 16], 180, 360, fill=(180, 210, 240), width=2)
    d.polygon([(420, 560), (780, 560), (720, 620), (480, 620)], fill=(120, 60, 30))
    d.line([(600, 220), (600, 560)], fill=(80, 50, 30), width=8)
    d.polygon([(610, 230), (610, 545), (790, 545)], fill=(250, 250, 250))
    d.polygon([(590, 260), (590, 545), (450, 545)], fill=(240, 240, 235))
    return im


def fireworks():
    rnd = random.Random(13)
    im = Image.new("RGB", (1200, 800), (5, 5, 20))
    d = ImageDraw.Draw(im)
    for cx, cy in [(300, 250), (700, 180), (950, 380), (500, 450)]:
        col = rnd.choice([(255, 80, 80), (80, 200, 255), (255, 220, 60), (180, 90, 255), (90, 255, 140)])
        for a in range(0, 360, 10):
            r = rnd.randint(110, 170)
            x, y = cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))
            d.line([(cx, cy), (x, y)], fill=col, width=3)
            d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(255, 255, 230))
    d.rectangle([0, 740, 1200, 800], fill=(10, 10, 10))
    return im


def flower():
    im = Image.new("RGB", (900, 900), (200, 235, 210))
    d = ImageDraw.Draw(im)
    d.line([(450, 470), (450, 890)], fill=(40, 140, 40), width=18)
    d.ellipse([300, 650, 450, 720], fill=(50, 160, 50))
    d.ellipse([450, 560, 600, 630], fill=(50, 160, 50))
    for a in range(0, 360, 36):
        cx, cy = 450 + 150 * math.cos(math.radians(a)), 330 + 150 * math.sin(math.radians(a))
        d.ellipse([cx - 95, cy - 95, cx + 95, cy + 95], fill=(240, 90, 150))
    d.ellipse([360, 240, 540, 420], fill=(250, 200, 30))
    return im


def rainbow():
    im = Image.new("RGB", (1200, 800))
    grad(im, (110, 170, 230), (200, 225, 245))
    d = ImageDraw.Draw(im)
    cols = [(230, 40, 40), (250, 140, 30), (250, 230, 40), (60, 190, 70), (50, 120, 230), (90, 60, 180), (150, 70, 200)]
    for i, c in enumerate(cols):
        r = 520 - i * 34
        d.arc([600 - r, 760 - r, 600 + r, 760 + r], 180, 360, fill=c, width=34)
    for cx, cy in [(200, 150), (950, 120)]:
        for k in range(4):
            d.ellipse([cx + k * 45, cy - (k % 2) * 25, cx + k * 45 + 110, cy + 70], fill="white")
    d.rectangle([0, 700, 1200, 800], fill=(80, 170, 70))
    return im


def clock():
    im = Image.new("RGB", (900, 900), (235, 230, 220))
    d = ImageDraw.Draw(im)
    d.ellipse([100, 100, 800, 800], fill="white", outline=(40, 40, 40), width=22)
    f = _font(56)
    for h in range(1, 13):
        a = math.radians(h * 30 - 90)
        d.text((450 + 270 * math.cos(a) - 18, 450 + 270 * math.sin(a) - 30), str(h), font=f, fill="black")
    for m in range(60):
        a = math.radians(m * 6)
        r0 = 320 if m % 5 else 300
        d.line([(450 + r0 * math.cos(a), 450 + r0 * math.sin(a)), (450 + 335 * math.cos(a), 450 + 335 * math.sin(a))],
               fill="black", width=3)
    d.line([(450, 450), (450 + 150 * math.cos(math.radians(-30)), 450 + 150 * math.sin(math.radians(-30)))], fill="black", width=16)
    d.line([(450, 450), (450, 200)], fill="black", width=9)
    d.ellipse([435, 435, 465, 465], fill=(200, 30, 30))
    return im


def chess():
    im = Image.new("RGB", (880, 880), (90, 60, 40))
    d = ImageDraw.Draw(im)
    s = 100
    for r in range(8):
        for c in range(8):
            d.rectangle([40 + c * s, 40 + r * s, 40 + (c + 1) * s, 40 + (r + 1) * s],
                        fill=(240, 217, 181) if (r + c) % 2 == 0 else (181, 136, 99))
    rnd = random.Random(14)
    for _ in range(20):
        r, c = rnd.randint(0, 7), rnd.randint(0, 7)
        col = (250, 250, 250) if rnd.random() < 0.5 else (20, 20, 20)
        x, y = 40 + c * s + 50, 40 + r * s + 50
        d.ellipse([x - 22, y - 38, x + 22, y + 2], fill=col, outline=(120, 120, 120))
        d.polygon([(x - 32, y + 38), (x, y - 10), (x + 32, y + 38)], fill=col, outline=(120, 120, 120))
    return im


def chat():
    im = Image.new("RGB", (720, 1480), (236, 229, 221))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 720, 150], fill=(7, 94, 84))
    d.ellipse([30, 40, 110, 120], fill=(200, 200, 200))
    f, fb = _font(30), _font(38)
    d.text((140, 55), "Kunal", font=fb, fill="white")
    msgs = [(0, "hey are you coming tonight?"), (1, "yes! what time?"), (0, "8 pm at the cafe"),
            (1, "ok see you there"), (0, "bring the charger pls"), (1, "sure"), (0, "also rent is due friday"),
            (1, "will transfer today"), (0, "thanks!")]
    y = 190
    for who, t in msgs:
        w = 30 + len(t) * 17
        x0 = 30 if who == 0 else 690 - w
        d.rounded_rectangle([x0, y, x0 + w, y + 80], 18, fill="white" if who == 0 else (220, 248, 198))
        d.text((x0 + 18, y + 22), t, font=f, fill="black")
        y += 120
    d.rectangle([0, 1390, 720, 1480], fill=(245, 245, 245))
    d.rounded_rectangle([20, 1405, 600, 1465], 30, fill="white")
    d.ellipse([620, 1400, 700, 1470], fill=(7, 94, 84))
    return im


def flowchart():
    im = Image.new("RGB", (1000, 1100), "white")
    d = ImageDraw.Draw(im)
    f = _font(30)

    def arrow(x0, y0, x1, y1):
        d.line([(x0, y0), (x1, y1)], fill="black", width=4)
        d.polygon([(x1 - 12, y1 - 18), (x1 + 12, y1 - 18), (x1, y1)], fill="black")

    d.ellipse([380, 40, 620, 130], outline="black", width=4); d.text((455, 70), "Start", font=f, fill="black")
    arrow(500, 130, 500, 200)
    d.rectangle([340, 200, 660, 300], outline="black", width=4); d.text((370, 235), "Read input data", font=f, fill="black")
    arrow(500, 300, 500, 380)
    d.polygon([(500, 380), (680, 500), (500, 620), (320, 500)], outline="black", width=4)
    d.text((440, 485), "Valid?", font=f, fill="black")
    arrow(500, 620, 500, 700)
    d.text((515, 640), "yes", font=f, fill="black")
    d.rectangle([340, 700, 660, 800], outline="black", width=4); d.text((380, 735), "Process data", font=f, fill="black")
    d.line([(680, 500), (820, 500), (820, 250), (660, 250)], fill="black", width=4); d.text((700, 460), "no", font=f, fill="black")
    arrow(500, 800, 500, 880)
    d.ellipse([380, 880, 620, 970], outline="black", width=4); d.text((460, 910), "End", font=f, fill="black")
    return im


def linechart():
    rnd = random.Random(15)
    im = Image.new("RGB", (1100, 700), "white")
    d = ImageDraw.Draw(im)
    for y in range(80, 620, 60):
        d.line([(100, y), (1050, y)], fill=(225, 225, 225), width=1)
    d.line([(100, 50), (100, 620), (1060, 620)], fill="black", width=3)
    pts = [(100 + i * 80, 560 - int(30 * i + rnd.randint(-60, 60))) for i in range(12)]
    d.line(pts, fill=(220, 60, 60), width=5)
    for x, y in pts:
        d.ellipse([x - 8, y - 8, x + 8, y + 8], fill=(220, 60, 60))
    f = _font(22)
    for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()):
        d.text((85 + i * 80, 635), m, font=f, fill="black")
    d.text((400, 10), "Monthly website visitors", font=_font(32), fill="black")
    return im


def calendar():
    im = Image.new("RGB", (1000, 900), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1000, 130], fill=(200, 40, 50))
    d.text((340, 30), "MARCH 2024", font=_font(64), fill="white")
    f = _font(30)
    for i, wd in enumerate("Mon Tue Wed Thu Fri Sat Sun".split()):
        d.text((40 + i * 135, 150), wd, font=f, fill=(90, 90, 90))
    day = 1
    for r in range(5):
        for c in range(7):
            x, y = 20 + c * 135, 200 + r * 135
            d.rectangle([x, y, x + 135, y + 135], outline=(190, 190, 190), width=2)
            if (r > 0 or c >= 4) and day <= 31:
                d.text((x + 10, y + 8), str(day), font=f, fill="black")
                day += 1
    return im


def slide():
    im = Image.new("RGB", (1600, 900), (250, 250, 252))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1600, 160], fill=(30, 60, 120))
    d.text((80, 45), "Q3 Product Roadmap", font=_font(72), fill="white")
    f = _font(44)
    for i, t in enumerate(["Launch mobile app beta", "Improve search relevance", "Reduce onboarding time by 30%",
                           "Expand to two new markets"]):
        d.ellipse([110, 260 + i * 130, 140, 290 + i * 130], fill=(30, 60, 120))
        d.text((170, 245 + i * 130), t, font=f, fill=(30, 30, 30))
    d.text((1450, 840), "3", font=_font(32), fill=(120, 120, 120))
    return im


def barcode():
    rnd = random.Random(16)
    im = Image.new("RGB", (1000, 560), "white")
    d = ImageDraw.Draw(im)
    x = 100
    while x < 900:
        w = rnd.choice([3, 3, 6, 9])
        if rnd.random() < 0.55:
            d.rectangle([x, 80, x + w, 420], fill="black")
        x += w + rnd.choice([3, 3, 6])
    d.text((260, 440), "8 901234 567890", font=_font(56), fill="black")
    return im


def moon():
    rnd = random.Random(17)
    im = Image.new("RGB", (1000, 800), (8, 10, 25))
    d = ImageDraw.Draw(im)
    for _ in range(60):
        x, y = rnd.randint(0, 999), rnd.randint(0, 799)
        d.ellipse([x, y, x + 2, y + 2], fill=(220, 220, 210))
    d.ellipse([330, 180, 670, 520], fill=(225, 225, 210))
    for _ in range(14):
        cx, cy, r = rnd.randint(380, 620), rnd.randint(230, 470), rnd.randint(10, 35)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(185, 185, 175))
    return im


def traffic():
    im = Image.new("RGB", (800, 1000), (170, 200, 230))
    d = ImageDraw.Draw(im)
    d.rectangle([370, 600, 430, 1000], fill=(60, 60, 60))
    d.rounded_rectangle([270, 80, 530, 620], 30, fill=(30, 30, 30))
    for i, c in enumerate([(230, 30, 30), (240, 190, 30), (40, 200, 70)]):
        d.ellipse([320, 120 + i * 165, 480, 280 + i * 165], fill=c if i != 1 else (90, 80, 30))
    d.rectangle([0, 900, 800, 1000], fill=(80, 80, 85))
    return im


SCENES = {
    "img_beach": (beach, "Pictures/Goa trip/IMG_20231223_164512.jpg"),
    "img_mountains": (mountains, "Pictures/Camera/IMG_20230114_101122.jpg"),
    "img_forest": (forest, "Pictures/Camera/IMG_20230305_090301.jpg"),
    "img_skyline": (skyline, "Pictures/Camera/IMG_20230618_221544.jpg"),
    "img_cake": (cake, "Pictures/Aai birthday/IMG-20240212-WA0004.jpg"),
    "img_house": (house, "Pictures/scan_kid_drawing.png"),
    "img_sailboat": (sailboat, "Pictures/Goa trip/IMG_20231224_120933.jpg"),
    "img_fireworks": (fireworks, "WhatsApp Images/IMG-20231112-WA0017.jpg"),
    "img_flower": (flower, "Pictures/Camera/IMG_20240401_074410.jpg"),
    "img_rainbow": (rainbow, "Pictures/Camera/IMG_20230722_173005.jpg"),
    "img_clock": (clock, "Pictures/Camera/IMG_20230901_120000.jpg"),
    "img_chess": (chess, "Pictures/Camera/IMG_20231010_200511.jpg"),
    "img_chat": (chat, "Screenshots/Screenshot_20240305-221144.png"),
    "img_flowchart": (flowchart, "Screenshots/Screenshot 2024-02-11 141502.png"),
    "img_linechart": (linechart, "Screenshots/Screenshot 2024-03-18 093012.png"),
    "img_calendar": (calendar, "Screenshots/Screenshot 2024-03-01 080000.png"),
    "img_slide": (slide, "Screenshots/Screenshot 2024-04-02 160045.png"),
    "img_barcode": (barcode, "Pictures/Camera/IMG_20240115_111111.jpg"),
    "img_moon": (moon, "Pictures/Camera/IMG_20231127_230215.jpg"),
    "img_traffic": (traffic, "Pictures/Camera/IMG_20230830_083015.jpg"),
}

# random-shape distractors (make_image) placed in realistic locations, some in topical folders
DISTRACTORS = (
    [f"Pictures/Camera/IMG_2023{m:02d}{d:02d}_1{d:01d}2233.jpg" for m, d in
     [(1, 3), (2, 7), (3, 9), (4, 2), (5, 5), (6, 1), (7, 8), (8, 4), (9, 6), (10, 3), (11, 2), (12, 9)]]
    + ["Pictures/Goa trip/IMG_20231225_101010.jpg", "Pictures/Goa trip/IMG_20231225_141516.jpg",
       "Pictures/Aai birthday/IMG-20240212-WA0007.jpg", "WhatsApp Images/IMG-20240101-WA0003.jpg",
       "WhatsApp Images/IMG-20240203-WA0011.jpg", "Downloads/image (2).png", "Downloads/download.jpg",
       "Pictures/Wallpapers/wallpaper_04.png"]
)
