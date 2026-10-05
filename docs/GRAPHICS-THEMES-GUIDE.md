# คู่มือ Graphics: ทำภาพ ปรับดีไซน์ และสร้างธีมเอง

คู่มือนี้พาทำตั้งแต่สร้างภาพจากธีมที่มีอยู่ ปรับดีไซน์เอง ให้ AI ปรับตามคำสั่ง เปลี่ยนลุคด้วย art style
ใส่รูปและตัดแปะ นำเข้า Affinity ไปจนถึงสร้างธีมใหม่ ทุกคำสั่งในคู่มือทดสอบกับเครื่องมือจริงแล้ว
รายละเอียดเชิงเทคนิคดูได้ที่ [README](../README.md) หัวข้อ Graphics

## 1. ภาพรวม: ระบบมี 3 ชั้น

| ชั้น | คืออะไร | อยู่ที่ไหน | ใครแก้ |
|---|---|---|---|
| **Motif** | ชิ้นส่วนวาดที่ใช้ซ้ำได้ เช่น ทุ่งดาว กริด ดวงอาทิตย์ข้อความ ปุ่ม | `src/book_production_platform/graphics_production/motifs.py` | นักพัฒนา (เขียน Python) |
| **Theme** | หน้าตาหนึ่งแบบ: ใช้ motif อะไร วางตรงไหน สีอะไร มีช่องข้อความอะไร | `src/book_production_platform/graphics_production/themes/*.json` | ใครก็ได้ (เขียน JSON) |
| **Art style** | "ลุค" ที่ใช้ได้กับทุกธีม: สี ฟอนต์ texture ของตกแต่ง และการแต่งรูป (retro, minimal, jazz, dystopia ...) | `src/book_production_platform/graphics_production/art_styles/*.json` + คลัง style ที่ AI สร้าง | ใครก็ได้ หรือให้ AI สร้างจากคำบรรยาย |
| **Design override** | ปรับงานชิ้นหนึ่งโดยไม่แก้ theme: เลือก art style สลับ style ซ่อน/เลื่อน layer เปลี่ยนค่า ใส่รูป | ในไฟล์ request ของงาน (`design`) | คน หรือ AI ตามคำสั่ง |

- ทำหน้าตาใหม่ = เขียน theme ใหม่ (ไม่ต้องเขียนโค้ด)
- ปรับงานชิ้นเดียว = ใส่ design override (ไม่ต้องแก้ theme)
- อยากได้ชิ้นส่วนวาดแบบใหม่ที่ยังไม่มี = เพิ่ม motif หนึ่งตัว แล้วทุก theme ใช้ได้
- อยากได้ลุคอื่น (เรโทร มินิมอล แจ๊ส ดิสโทเปีย หรืออะไรก็ได้) = เลือก art style (ข้อ 5A) ธีมเดิม ข้อความเดิม เปลี่ยนแค่ "หน้าตา"
- อยากใส่รูปลูกค้า ตัดพื้นหลัง ทำสติกเกอร์ ตัดแปะ = ใส่ `images` (ข้อ 5B)

## 2. เตรียมเครื่อง (ครั้งเดียว)

1. ใช้ Python environment ของโปรเจกต์ (คำสั่ง `book-production` อยู่ในนั้น)

   ```bash
   source /home/bookchaowalit/solo-empire/.runtime/venv-platform/bin/activate
   ```

2. ติดตั้งฟอนต์ที่ธีมใช้ (จาก Google Fonts, ใบอนุญาต OFL ใช้งานเชิงพาณิชย์ได้) ไม่งั้น Affinity และภาพ PNG จะใช้ฟอนต์อื่นแทน:
   **Sarabun, Kanit, Mitr, Chakra Petch, Trirong, Itim, Sriracha** (ไทย) และ **Anton, Bungee, Limelight,
   Monoton, Press Start 2P, Special Elite, Playfair Display, Space Mono** (ลาติน) ชุดนี้อยู่ใน
   `/home/bookchaowalit/solo-empire/.runtime/fonts` แล้ว ดับเบิลคลิกติดตั้งในเครื่องที่ใช้ Affinity
3. ตัวทำภาพ PNG: ถ้ามี Chrome/Edge ระบบใช้ให้เอง ถ้าไม่มีจะใช้ `resvg-py`
   (`pip install .[preview]`) ถ้าฟอนต์อยู่นอกโฟลเดอร์ระบบ ให้บอกที่อยู่:

   ```bash
   export GRAPHICS_PRODUCTION_FONT_DIRS=/home/bookchaowalit/solo-empire/.runtime/fonts
   export GRAPHICS_PRODUCTION_FALLBACK_FONT=Sarabun
   ```

## 3. ทำภาพจากธีมที่มีอยู่

ดูว่ามีธีมอะไร ขนาดไหน ช่องข้อความอะไร และ style preset อะไร:

```bash
book-production graphics themes
```

ธีมตอนนี้: `synthwave`, `bold_editorial`, `soft_gradient`, `menu_board`, `promo_sale`,
`grand_opening` และของเดิม `quote_card`, `product_card`, `announcement` ขนาด: `square` 1080×1080,
`portrait` 1080×1350, `story` 1080×1920, `landscape` 1920×1080 (ธีมเดิม 3 ตัวไม่มี landscape)

สร้างภาพ (ใส่ข้อความด้วย `--set ช่อง=ข้อความ` ได้หลายครั้ง, `--variant all` = ทุกขนาด):

```bash
book-production graphics make --theme synthwave --variant portrait \
  --set headline=SOLO --set headline2=EMPIRE --set "subhead=สร้างอาณาจักร · คนเดียว" \
  --primary "#14213D" --accent "#FCA311" \
  --output work --idempotency-key solo-1
```

ได้ไฟล์:

- `work/<batch-id>/synthwave-portrait.svg` ไฟล์แก้ไขได้ (เปิดใน Affinity)
- `work/<batch-id>/synthwave-portrait.png` ภาพตัวอย่าง (ส่งลูกค้าได้)
- `work/synthwave-portrait.request.json` ไฟล์ตั้งต้นสำหรับปรับต่อ (ข้อ 4 และ 5)

`--idempotency-key` คือชื่องาน ถ้าใช้ชื่อเดิมกับข้อมูลเดิม ระบบจะคืนผลเดิมโดยไม่ทำใหม่
ถ้าเปลี่ยนข้อมูลให้ใช้ชื่อใหม่

## 4. ปรับดีไซน์เอง (ไม่ใช้ AI)

### 4.1 ดูว่าปรับอะไรได้บ้าง

```bash
book-production graphics controls --request work/synthwave-portrait.request.json
```

จะได้รายการ:

- `styles` ชุดสีสำเร็จรูปของธีม
- `palette` สีทั้งหมดที่เปลี่ยนได้ (ชื่อ → ค่าสี)
- `slots` ช่องข้อความ พร้อมคำที่ใช้เรียก
- `layers` ทุก layer และทุกชิ้นในนั้น เช่น `Title/headline` พร้อม `params` ค่าปัจจุบัน
  (ตำแหน่ง `x`, `y`, ขนาด `width`, `height`, `font_size`, สี `color`, `fill`, ความโปร่ง `opacity` ฯลฯ)

### 4.2 เขียนไฟล์ patch

สร้างไฟล์ `patch.json` ใส่เฉพาะสิ่งที่จะเปลี่ยน:

```json
{
  "style": "sunrise",
  "palette": {"cyan": "#00E5FF"},
  "layers": {
    "Stars": {"visible": false},
    "Mountains": {"opacity": 0.6, "dy": 30}
  },
  "items": {
    "Title/headline": {"params": {"y": "V + 30", "font_size": "V * 1.1"}},
    "Sun/sun": {"params": {"r": "V * 1.2"}},
    "Subtitle/subhead": {"params": {"weight": "bold", "color": "#FFFFFF"}}
  },
  "content": {"subhead": "เริ่มต้นจากศูนย์"}
}
```

กติกาของค่า:

| เขียนแบบนี้ | ความหมาย |
|---|---|
| `120` | ค่าตายตัว (พิกเซล) |
| `"V + 30"` / `"V - 30"` | จากค่าเดิม บวก/ลบ (แกน y ยิ่งมากยิ่งลงล่าง แกน x ยิ่งมากยิ่งไปขวา) |
| `"V * 1.2"` | ใหญ่ขึ้น 20% |
| `"W * 0.5"`, `"H - 100"`, `"MIN * 0.1"` | อิงขนาดภาพ: `W` กว้าง `H` สูง `CX` `CY` จุดกลาง `MIN` `MAX` ด้านสั้น/ยาว |
| `"#FF0055"` หรือ `"palette.pink"` | สี (hex หรือชื่อสีใน palette) |
| `"weight": "bold"` | `normal`, `bold`, `black` |
| `"align": "center"` | `left`, `center`, `right` |
| `"visible": false` | ซ่อน (ทั้ง layer หรือชิ้นเดียว) |
| `"dx"`, `"dy"` ของ layer | เลื่อนทั้ง layer (พิกเซล) |

### 4.3 ใช้ patch

```bash
book-production graphics edit --request work/synthwave-portrait.request.json \
  --patch patch.json --output v2 --job-id solo-v2
```

ได้ `v2/design-request.json` (ดีไซน์ที่ปรับแล้ว) และ SVG/PNG ใหม่ใน `v2/<batch-id>/`
จะปรับต่อก็ส่ง `v2/design-request.json` เป็น `--request` รอบถัดไป ค่าทุกครั้งจะสะสมต่อกัน

ถ้าค่าไม่ถูกต้อง ระบบจะบอกเหตุผลแทนการทำภาพผิด (ดูข้อ 9)

## 5. ให้ AI ปรับตามคำสั่ง

ใช้แทน patch ได้เลย พิมพ์สิ่งที่อยากเปลี่ยนเป็นภาษาไทยหรืออังกฤษ:

```bash
book-production graphics edit --request work/synthwave-portrait.request.json \
  --instruction "เปลี่ยนเป็นสไตล์ ice ซ่อนดาว และขยายดวงอาทิตย์ 15%" \
  --output v2 --job-id solo-ai-1 \
  --provider-config /home/bookchaowalit/solo-empire/.runtime/provider-configs/graphics-edit.json
```

สั่งต่อจากผลเดิม: ส่ง `v2/design-request.json` เป็น `--request` แล้วตั้ง `--job-id` ใหม่ทุกครั้ง

คำสั่งที่ได้ผลดี (บอกว่าส่วนไหน + ทำอะไร + เท่าไร):

- "เลื่อนหัวข้อ SOLO ลง 40px"
- "ทำให้ภูเขาจางลงเหลือ 50%"
- "เปลี่ยนคำโปรยเป็น 'เริ่มต้นจากศูนย์'" (ข้อความใหม่ใส่ในเครื่องหมายคำพูด)
- "ใช้สไตล์ sunrise และซ่อนกรอบ"

ระบบจะถามกลับ (`needs_input`) แทนการเดาเมื่อ:

- ไม่บอกว่าส่วนไหน เช่น "ปรับให้สวยขึ้น", "เปลี่ยนสีหน่อย"
- ขอสิ่งที่ธีมทำไม่ได้ เช่น "เพิ่มรูปยูนิคอร์น" (ต้องเพิ่ม motif หรือใช้ Affinity)
- ให้ AI แต่งข้อความเอง เช่น "เปลี่ยนหัวข้อเป็นข้อความที่ขายดี"
- ผลลัพธ์ทำให้ข้อความหลุดขอบภาพ หรือยาวเกินกล่อง

AI เห็นแค่รายการค่าที่ปรับได้ (ข้อ 4.1) ไม่เห็นข้อความในงาน และทุกค่าที่เสนอถูกตรวจซ้ำก่อนใช้

**ต้องเปิดใช้ก่อน:** ไฟล์ `graphics-edit.json` เริ่มที่ `"mode": "shadow"` (ทดสอบอย่างเดียว)
คำสั่งจะตอบ `shadow_mode_only` จนกว่าเจ้าของจะเปลี่ยนเป็น `"mode": "live"` หลังดูผลทดสอบ:

```bash
book-production graphics edit-shadow \
  --provider-config /home/bookchaowalit/solo-empire/.runtime/provider-configs/graphics-edit.json \
  --cases tests/graphics/fixtures/eval_gfx_edit24.json \
  --split tests/graphics/fixtures/eval_gfx_edit24_split.json --output edit-shadow.json
```

## 5A. เปลี่ยนลุคทั้งงานด้วย art style

ธีมคือ "วางอะไรตรงไหน" ส่วน art style คือ "หน้าตาเป็นแบบไหน" ใช้ art style ไหนกับธีมไหนก็ได้
ดูรายการ:

```bash
book-production graphics styles
```

มีให้ 18 แบบ: `retro` (ยุค 70), `minimal`, `jazz`, `dystopia`, `vaporwave`, `art_deco`, `grunge`,
`swiss`, `memphis`, `y2k`, `pop_art`, `noir`, `cyberpunk`, `risograph`, `luxury`, `kawaii`,
`handmade`, `arcade_8bit`

art style หนึ่งชุดเปลี่ยน:

- **สี** ตามหน้าที่ (พื้น การ์ด ตัวหนังสือ สีเน้น ปุ่ม) ทุกธีมจับคู่สีของตัวเองกับหน้าที่ไว้แล้ว
  ระบบตรวจ contrast ของตัวหนังสือตามเกณฑ์ WCAG ทุก style จึงไม่มีทางได้ตัวหนังสือที่อ่านไม่ออก
- **ฟอนต์** หัวข้อ เนื้อหา และป้ายเล็ก (มีฟอนต์ไทยรองรับทุกชุด)
- **ลักษณะหัวข้อ** น้ำหนัก ตัวพิมพ์ใหญ่ ระยะตัวอักษร เงาเยื้อง
- **ของตกแต่งและ texture** เช่น โน้ตดนตรี กรอบอาร์ตเดโค แถบเตือน scanline รอยขีดข่วน
  ทุกอย่างเป็น layer แยก (`ArtBackdrop`, `ArtOverlay`) จึงซ่อนหรือแก้ใน Affinity ได้
- **การแต่งรูป** ค่าเริ่มต้นของรูปที่ใส่ (เช่น jazz ทำ duotone, noir ทำขาวดำ, kawaii ใส่ขอบสติกเกอร์)
- บาง style (minimal, luxury, swiss, noir) ซ่อนของตกแต่งเดิมของธีม บาง style แค่ทำให้จางลง

ใช้ตอนสร้าง:

```bash
book-production graphics make --theme promo_sale --variant all \
  --set headline=ฉลองหน้าหนาว --set discount=-20% --set cta=แวะมาเลย \
  --art-style jazz --output work --idempotency-key jazz-1
```

`--keep-brand-colors` ใช้สีแบรนด์ของลูกค้าเป็นสีเน้นแทนสีของ style
ใช้กับงานที่ทำไว้แล้ว: patch `{"art_style": "noir"}` หรือสั่ง AI ว่า "เปลี่ยนเป็นแนวฟิล์มนัวร์"
เอาออก: `{"art_style": "none"}`

**อยากได้ลุคที่ยังไม่มี** ("งานวัดไทย ไฟกระพริบ", "ฝนตกโตเกียว lo-fi") ให้ AI ออกแบบ style ใหม่จากคำบรรยาย
AI เลือกสี ฟอนต์ และของตกแต่งจากชิ้นส่วนที่ระบบมีอยู่ ไม่ได้เขียนโค้ดหรือวาดเอง จากนั้นระบบตรวจ contrast
และลองทำภาพจริง 3 แบบก่อนเก็บเข้าคลัง ถ้าไม่ผ่านจะให้ AI แก้หนึ่งรอบ ถ้ายังไม่ผ่านจะไม่บันทึก:

```bash
export GRAPHICS_PRODUCTION_STYLE_DIR=/home/bookchaowalit/solo-empire/.runtime/graphics-styles
book-production graphics style-create --description "งานวัดไทย ไฟกระพริบ สีสด" \
  --job-id style-temple-1 --output style-preview \
  --provider-config /home/bookchaowalit/solo-empire/.runtime/provider-configs/graphics-art-style.json
```

style ที่ได้จะอยู่ในคลังและใช้แบบเดียวกับ style ในตัว ถ้าส่ง `--style-provider-config` ให้คำสั่ง `edit`
แล้วสั่งลุคที่ยังไม่มี ระบบจะสร้าง style ให้ก่อน แล้วใช้กับงานนั้นทันที
(config ต้องเป็น `"mode": "live"` เหมือนข้อ 5)

ถ้าลูกค้าเขียนลุคไว้ในบรีฟ เช่น "อยากได้แนวมินิมอล" คำสั่ง `graphics brief` จะใส่ art style ให้เองจากคำในบรีฟ
(ไม่ได้ให้ AI เดา)

## 5B. ใส่รูป ตัดพื้นหลัง ทำสติกเกอร์ ตัดแปะ

ใส่รูปได้ทุกธีม ได้หลายรูป (สูงสุด 12) ตำแหน่งและขนาดเป็น**สัดส่วนของภาพ** (0–1 นับจากมุมซ้ายบน)
จึงใช้ค่าเดียวกันได้ทุกขนาด:

```bash
book-production graphics make --theme grand_opening --variant portrait \
  --set shop=คลินิกสัตว์หางกระดิก --set "headline=เปิดสาขาใหม่แล้ว!" --set "date=เสาร์ 12 ธันวาคม 2026" \
  --art-style kawaii --photo corgi=photos/corgi.jpg \
  --place '{"id":"corgi","asset":"corgi","x":0.6,"y":0.6,"width":0.42,"height":0.4,"z":"front","rotation":-6,
            "effects":{"cutout":"ai","fit":"contain","outline_width":16,"shadow":0.6}}' \
  --output work --idempotency-key vet-1
```

ค่าของแต่ละรูป:

| ค่า | ความหมาย |
|---|---|
| `x`, `y`, `width`, `height` | กรอบรูป (สัดส่วน 0–1) `square: true` ให้สูงเท่ากว้าง |
| `z` | `background` (ทับพื้นหลัง), `behind_text` (ใต้ตัวหนังสือ, ค่าเริ่มต้น), `front` (บนสุด แบบสติกเกอร์) |
| `rotation`, `opacity` | หมุน (องศา) และความโปร่งใส |
| `effects.fit` | `cover` เต็มกรอบ (ครอปตาม `focus_x`/`focus_y`, ซูมด้วย `zoom`) หรือ `contain` เห็นทั้งรูป |
| `effects.cutout` | ลบพื้นหลัง: `ai` (คน สัตว์ สินค้า, ใช้โมเดลในเครื่อง) หรือ `plain` (รูปพื้นขาว/สีเรียบ) |
| `effects.mask` | รูปทรง: `rect rounded circle ellipse arch blob star hexagon torn` (`torn` = ขอบกระดาษฉีก) |
| `effects.filter` | `style` (ตาม art style), `none grayscale sepia duotone posterize high_contrast blur invert vintage halftone` |
| `effects.outline_width`, `outline_color` | ขอบสติกเกอร์รอบรูปทรงที่เห็น |
| `effects.shadow` | เงา 0–1 |
| `effects.brightness`, `contrast`, `saturation`, `flip_x` | ปรับแสงสี กลับซ้ายขวา |

สีใน effects ใส่ได้ทั้ง `#RRGGBB` และสีตามหน้าที่ของ art style เช่น `role.accent`

สั่ง AI ได้เหมือนกัน เช่น "เอารูปหมาไปไว้มุมขวาล่าง ตัดพื้นหลังออก ใส่ขอบขาว", "ทำรูปแมวเป็นวงกลมแล้วเอียงนิดหน่อย",
"ให้รูปพื้นหลังเป็นขาวดำจาง ๆ" ถ้ารูป `front` บังตัวหนังสือเกิน 15% ระบบจะถามกลับ (`photo_covers_text`) แทนการทำต่อ

การตัดพื้นหลังแบบ `ai` ต้องติดตั้งครั้งเดียว (`pip install .[cutout]`) และโหลดโมเดลมาเก็บไว้ในเครื่อง
(`U2NET_HOME=/home/bookchaowalit/solo-empire/.runtime/u2net`) ถ้าไม่มีจะใช้ `plain` แทนและบันทึกไว้ใน trace
รูปที่แต่งแล้วฝังใน SVG เป็น PNG ขนาดเท่ากรอบ ถ้าจะแก้ต่อใน Affinity ให้ใช้รูปต้นฉบับ
โดยทำตามขั้นตอนใน `MANUAL-STEPS.md` (มีขั้นตอนลบพื้นหลัง ครอป มาสก์ ฟิลเตอร์ ขอบ และเงาของแต่ละรูป)

## 6. นำเข้า Affinity และส่งลูกค้า

1. Affinity Designer/Photo: `File ▸ Open…` เลือกไฟล์ `.svg`
2. แต่ละ layer ในธีมจะเป็น group ชื่อเดียวกัน (Background, Stars, Sun, Title ...)
   ข้อความยังพิมพ์แก้ได้ gradient แก้ได้ใน Fill
3. ฟอนต์ไม่ตรง = ยังไม่ได้ติดตั้งฟอนต์ในข้อ 2
4. ส่งลูกค้า: ใช้ไฟล์ `.png` ที่ได้ หรือ Export จาก Affinity
   ต้องการไฟล์ความละเอียดสูง/PDF สำหรับพิมพ์:

   ```bash
   book-production graphics export --manifest work/manifest-<id>.json --output print --scale 3 --dpi 300
   ```

ถ้าแก้ใน Affinity แล้ว ค่าจะไม่ย้อนกลับมาที่ระบบ ควรปรับผ่าน patch/AI ให้เสร็จก่อน แล้วค่อยเก็บงานละเอียดใน Affinity

## 7. สร้างธีมใหม่

### 7.1 เริ่มจากตัวอย่าง

คัดลอกตัวอย่าง [`examples/graphics/sale_burst.theme.json`](../examples/graphics/sale_burst.theme.json)
(ป้ายลดราคา: พื้นสีสด แสงแฉก ตัวเลขใหญ่ ปุ่ม) ไปไว้ที่
`src/book_production_platform/graphics_production/themes/<ชื่อธีม>.json` แล้วแก้

### 7.2 โครงไฟล์

```json
{
  "schema_version": "graphic-theme.v1",
  "theme_id": "sale_burst",
  "title": "Sale burst",
  "description": "อธิบายสั้นๆ",
  "ir_version": "graphic-ir.v2",
  "template_version": "1",
  "variants": ["square", "portrait", "story"],
  "evidence": ["sale", "discount", "ลดราคา"],
  "slots": {
    "headline": {"required": true, "labels": ["headline", "หัวข้อ"]},
    "cta": {"required": false, "labels": ["button", "ปุ่ม"]}
  },
  "styles": {"mint": {"title": "Mint", "palette": {"bg": "#0F766E"}}},
  "palette": {"bg": "#E11D48", "accent": "#FACC15", "ink": "#FFFFFF"},
  "paints": [],
  "vars": {"S": "MIN / 1080"},
  "layers": [
    {"id": "Background", "items": [ {"motif": "rect", "id": "bg", "fill": "palette.bg"} ]},
    {"id": "Text", "items": [ ... ]}
  ]
}
```

| ช่อง | ความหมาย |
|---|---|
| `theme_id` | ชื่อเรียก (ตัวเล็ก ตัวเลข `_`) ห้ามซ้ำกับธีมอื่น ควรตั้งชื่อไฟล์ให้ตรงกัน |
| `variants` | ขนาดที่รองรับ |
| `evidence` | คำในบรีฟที่หมายถึงธีมนี้ (ให้ AI เลือกธีมจากบรีฟ) |
| `slots` | ช่องข้อความ: `required` บังคับไหม, `labels` คำที่คนใช้เรียก (ไทย+อังกฤษ) |
| `palette` | ชื่อสี → ค่าสี หรือ `brand.primary` / `brand.accent` / `brand.foreground` / `brand.background` (ใช้สีแบรนด์ของงาน) |
| `styles` | ชุดสีสำเร็จรูป: เปลี่ยนสีใน palette บางตัว |
| `paints` | gradient ที่ตั้งชื่อไว้ใช้ซ้ำ: `{"paint_id": "sky", "type": "linear", "angle": 90, "stops": [{"offset": 0, "color": "palette.bg"}, ...]}` (`type` = `linear` หรือ `radial`) อ้างด้วย `"paint:sky"` |
| `vars` | ตัวแปรที่คำนวณครั้งเดียวแล้วใช้ได้ทั้งไฟล์ |
| `layers` | เรียงจากล่างขึ้นบน แต่ละ layer มี `items` (motif) และกลายเป็น layer ชื่อเดียวกันใน Affinity |

เคล็ดลับให้สลับ style ได้ทั้งภาพ: ทุกสีควรอ้าง `palette.<ชื่อ>` ไม่ใส่ค่าสีตรงๆ ใน item

### 7.3 ให้หนึ่งธีมใช้ได้ทุกขนาด

- เขียนตำแหน่งเป็นสัดส่วน: `"x": "W * 0.08"`, `"font_size": "MIN * 0.08"`
- ค่าต่างกันตามขนาด: `{"by_variant": {"landscape": "H * 0.7", "default": "H * 0.6"}}`
- ขึ้นกับว่ามีข้อความหรือรูปไหม: `{"if_slot": "headline2", "then": 400, "else": 300}`, `{"if_asset": "product_image", "then": 670, "else": 260}`
- แสดงชิ้นนี้เฉพาะเมื่อ: `"when": {"slot": "cta"}` / `{"variant": ["story"]}` / `{"asset": "product_image"}`
- ฟังก์ชันที่ใช้ได้ในสูตร: `min`, `max`, `round`, `abs`, `sqrt`, `sin`, `cos` (องศา) และ `+ - * / % **`

### 7.4 Motif ที่มีให้ใช้

ทุก item ต้องมี `"motif"` และ `"id"` (ห้ามซ้ำใน layer) ค่าที่ไม่ใส่จะใช้ค่าเริ่มต้น

| motif | ใช้ทำ | ค่าหลัก |
|---|---|---|
| `rect` | สี่เหลี่ยม พื้นหลัง ปุ่ม | `x y width height fill stroke stroke_width rx opacity` |
| `ellipse` | วงกลม/วงรี | `cx cy r` (หรือ `rx ry`) `fill stroke opacity` |
| `line` | เส้นตรง | `x1 y1 x2 y2 stroke stroke_width` |
| `polygon` | รูปหลายเหลี่ยม | `points: [[x,y],...] fill stroke closed` |
| `glow` | แสงฟุ้งวงกลม | `cx cy r color strength` |
| `text_block` | ข้อความในกล่อง (ย่ออัตโนมัติถึง `min_font_size`) | `slot` หรือ `text`, `x y width height font_size min_font_size max_lines font weight letter_spacing align color stroke opacity format` |
| `display_text` | หัวข้อใหญ่มีเงาซ้อน | เหมือน `text_block` + `shadows: [{dx, dy, color, opacity}]` |
| `image` | รูปสินค้าจาก assets | `asset x y width height` |
| `starfield` | ดาวสุ่ม + ประกาย | `x y width height count sizes sparkles sparkle_size color` |
| `perspective_grid` | พื้นกริดย้อนยุค | `horizon bottom vanish_x spread near_spread verticals horizontals power floor vertical_color horizontal_color horizon_color` |
| `striped_sun` | ดวงอาทิตย์ขีด | `cx cy r cut_y slat_start fill first_gap first_band gap_growth band_shrink` |
| `ridge` | แนวภูเขา | `base` + `points` หรือ (`peaks min_height max_height x_from x_to`), `fill stroke` |
| `waves` | คลื่นซ้อน | `y count step amplitude wavelength colors opacity` |
| `blobs` | ก้อนรูปทรงอิสระ | `count min_radius max_radius colors opacity wobble` |
| `halftone` | จุดไล่ขนาดแบบงานพิมพ์ | `x y width height spacing min_radius max_radius direction color` |
| `rays` | แสงแฉก | `cx cy count inner outer rotation color opacity` |
| `rings` | วงซ้อน | `cx cy radius step count stroke stroke_width` |
| `stripes` | ลายเส้นเฉียง | `x y width height band gap angle color opacity` |
| `grain` | เกรนผิวกระดาษ | `count size color opacity` |
| `corner_frame` | กรอบมุม + เส้นขอบ | `inset length color stroke_width border border_width` |
| `scanlines` | เส้นจอ CRT | `spacing band color opacity` |
| `checker` | ตารางหมากรุก | `x y width height size color opacity` |
| `dots` | ลายจุด | `spacing radius stagger color opacity` |
| `confetti_shapes` | สามเหลี่ยม วง เส้นหยัก กากบาท (Memphis) | `count size colors avoid_center` |
| `squiggle` | เส้นหยักวาดมือ | `x1 y1 x2 y2 amplitude wavelength stroke stroke_width` |
| `zigzag` | เส้นซิกแซก | `y amplitude step stroke stroke_width` |
| `arcs` | ครึ่งวงซ้อน (พัดอาร์ตเดโค/พระอาทิตย์ขึ้น) | `cx cy radius step count stroke` |
| `deco_frame` | กรอบคู่มุมขั้นบันได | `inset gap length color stroke_width` |
| `distress` | รอยหมึกกระเด็นและรอยขีด (grunge) | `count size color opacity` |
| `notes` | โน้ตดนตรี | `x y width height count size color` |
| `glitch_bars` | แถบ glitch สองสี | `count colors max_height opacity` |
| `barcode` | บาร์โค้ดตกแต่ง | `x y width height color` |
| `vignette` | ขอบมืด | `color strength` |
| `torn_edge` | แถบกระดาษฉีก | `y height side amplitude fill` |

ฟอนต์ใน `font`: `request` (ฟอนต์ของงาน), `builtin:sans`, `builtin:display` (Anton),
`builtin:thai` (Sarabun), `builtin:serif` (Playfair Display), `builtin:mono` (Space Mono),
`builtin:kanit`, `builtin:mitr`, `builtin:chakra` (Chakra Petch), `builtin:trirong`, `builtin:itim`,
`builtin:sriracha`, `builtin:anton`, `builtin:bungee`, `builtin:limelight`, `builtin:monoton`,
`builtin:pixel` (Press Start 2P), `builtin:typewriter` (Special Elite)

ธีมใหม่ที่อยากให้ art style ใช้ได้: ใส่ `"roles": {"ชื่อสีใน palette": "bg|bg2|surface|ink|accent|accent2|highlight|on_accent|muted"}`
และติด `"decor": true` ที่ layer หรือ item ที่เป็นของตกแต่ง (style แบบ minimal จะซ่อนให้)

`fill` / `stroke` / `color` ของ `rect`, `ellipse`, `polygon`, `ridge`, `line` และข้อความ ใส่เป็น gradient ในตัวก็ได้:
`"fill": {"type": "linear", "angle": 90, "stops": [...]}`

### 7.5 ทดสอบธีมใหม่

```bash
book-production graphics themes                 # ธีมใหม่ต้องขึ้นในรายการ
book-production graphics make --theme sale_burst --variant all \
  --set headline=ลดกระหน่ำปลายปี --set discount=-50% --set cta=ช้อปเลย \
  --output test-sale --idempotency-key sale-1
```

เปิด PNG ทุกขนาดดูว่าไม่มีอะไรทับกัน จากนั้นลอง style:
สร้าง patch `{"style": "mint"}` แล้วใช้ `edit --patch` (ข้อ 4.3)

ก่อน commit ธีมใหม่ (สำหรับนักพัฒนา):

1. เพิ่มข้อความตัวอย่างของธีมใน `SAMPLE` ที่ `tests/graphics/test_themes.py`
2. รัน `python -m pytest tests/graphics -q`
3. ธีมใหม่จะเข้าไปอยู่ในรายการที่ AI ใช้เลือกธีมจากบรีฟทันที ให้รัน `graphics brief-shadow`
   ซ้ำกับ AI จริงเพื่อยืนยันว่าเกณฑ์เดิมยังผ่าน

## 8. เพิ่ม motif ใหม่ (สำหรับนักพัฒนา)

1. เขียนฟังก์ชัน `def my_motif(ctx, spec) -> list[...]` ใน `motifs.py`
   ใช้ `ctx.num(...)` อ่านตัวเลข/สูตร, `ctx.color(...)` อ่านสี, `ctx.rng(item_id)` สำหรับสุ่มแบบคงที่
   และ `_path_node(...)` สร้างรูปทรง
2. ลงทะเบียนใน `MOTIFS = {...}`
3. ถ้ามีค่าตัวเลขใหม่ที่อยากให้ปรับผ่าน override/AI ได้ ให้เพิ่มชื่อใน `EDITABLE_NUMBERS` (`themes.py`)
4. เขียน test แล้วใช้ในธีมได้ทันที

## 9. แก้ปัญหาที่พบบ่อย

| ข้อความที่เห็น | สาเหตุ | แก้ |
|---|---|---|
| `content_required:<ช่อง>` | ไม่ได้ใส่ช่องข้อความที่บังคับ | เพิ่ม `--set <ช่อง>=...` |
| `text_overflow` / `text_does_not_fit` | ข้อความยาวเกินกล่อง | ย่อข้อความ หรือขยาย `width`/`height` ของกล่อง |
| `variant_unsupported` | ธีมไม่มีขนาดนี้ | ดูขนาดใน `graphics themes` |
| `template_unknown` | ไม่มีธีมชื่อนี้ หรือไฟล์ JSON ผิดรูปแบบ | ตรวจ `theme_id` และชื่อไฟล์ |
| `design_item_unknown:<Layer/item>` | อ้างชิ้นที่ไม่มี | ดูชื่อใน `graphics controls` |
| `design_param_not_editable:<...>` | ค่านี้ปรับไม่ได้ (เช่น `motif`, `slot`) | ใช้เฉพาะค่าที่อยู่ใน `params` ของ controls |
| `design_style_unknown` | ไม่มี style ชื่อนี้ | ดู `styles` ใน controls |
| `out_of_canvas` | ปรับแล้วข้อความหลุดขอบภาพ | ลดค่าที่เลื่อน |
| `instruction_unclear` / `unsupported_request` | AI ไม่แน่ใจว่าส่วนไหน หรือทำไม่ได้ | บอกให้ชัดขึ้น (ข้อ 5) |
| `shadow_mode_only` | config ของ AI ยังไม่เปิดใช้จริง | เจ้าของเปลี่ยนเป็น `"mode": "live"` |
| `browser_not_found` | ไม่มีตัวทำ PNG | ติดตั้ง Chrome/Edge หรือ `resvg-py` |
| `art_style_unknown:<id>` | ไม่มี art style ชื่อนี้ | ดู `graphics styles` หรือสร้างด้วย `style-create` |
| `art_style_low_contrast` | style ที่ออกแบบทำให้ตัวหนังสืออ่านยาก | ปรับสี ink/bg (ระบบไม่ยอมบันทึก) |
| `art_style_needs_designer` | สั่งลุคที่ยังไม่มี แต่ไม่ได้ส่ง `--style-provider-config` | ส่ง config หรือรัน `style-create` ก่อน |
| `design_image_asset_unknown:<key>` | `asset` ของรูปไม่อยู่ใน `assets` | ใส่ `--photo key=path` ให้ตรงชื่อ |
| `photo_covers_text` | รูป `front` บังตัวหนังสือ | ย่อ/เลื่อนรูป หรือใช้ `z: behind_text` |
| `image_effects_need_raster_image` | ใส่ effects กับไฟล์ SVG | ใช้ PNG/JPG แทน |
| ฟอนต์ไม่ตรง | ยังไม่ได้ติดตั้งฟอนต์ | ติดตั้งฟอนต์ทั้งหมดในข้อ 2 |
