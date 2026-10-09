"""Manual-reproduction guide for Marketing: build the same review-only drafts by hand.

The steps come from the ``CampaignIR`` the engine packaged, plus the tracked
links, A/B groups, and tag findings it computed (:mod:`.channel_tools`). For
each channel the guide names the real program (Meta Business Suite for
Facebook/Instagram, LinkedIn Page admin, LINE Official Account Manager,
Mailchimp for email, the site CMS for web), the field to paste each copy into
(with its character count against the engine limit), each image or video slot
with the frame size to prepare in Canva, and the link to paste. V1A never
publishes, schedules, sends, targets, or spends, so every channel ends with
"save as draft for review"; the audience rules are recorded for the approver
only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from marketing_ir import AssetSpec, CampaignIR
from production_trace import ManualGuide

from .channel_tools import hashtags_in, mentions_in
from .copy_rules import CHANNEL_LIMITS, copy_length

_PROGRAMS: dict[str, dict[str, str]] = {
    "facebook": {
        "program": "Meta Business Suite",
        "open": "business.facebook.com ▸ Home ▸ Create post",
        "placement": "Post to ▸ เลือกเพจ Facebook (ยกเลิกเลือก Instagram)",
        "text": "Post details ▸ Text",
        "media": "Media ▸ Add photo / Add video",
        "draft": "ปุ่มลูกศรข้าง Publish ▸ Save as draft",
        "link": "วางลิงก์ติดตามท้ายข้อความในช่อง Text (Facebook สร้างพรีวิวลิงก์ให้)",
        "ab": "Post details ▸ A/B test (ถ้าเพจมีเมนูนี้) ▸ ใส่ Version ตามตาราง; ถ้าไม่มีให้สร้างแบบร่างแยกต่อ variant",
    },
    "instagram": {
        "program": "Meta Business Suite",
        "open": "business.facebook.com ▸ Home ▸ Create post",
        "placement": "Post to ▸ เลือกบัญชี Instagram (ยกเลิกเลือก Facebook)",
        "text": "Post details ▸ Text (caption)",
        "media": "Media ▸ Add photo / Add video (Instagram ต้องมีสื่ออย่างน้อย 1 ชิ้น)",
        "draft": "ปุ่มลูกศรข้าง Publish ▸ Save as draft (ถ้าไม่มี ให้ปิดหน้าต่างแล้วเลือกเก็บแบบร่าง)",
        "link": "ลิงก์ใน caption กดไม่ได้: ใส่ลิงก์ติดตามใน Edit profile ▸ Links ของบัญชี หรือแจ้งผู้อนุมัติ",
        "ab": "Post details ▸ A/B test (ถ้าบัญชีมีเมนูนี้); ถ้าไม่มีให้สร้างแบบร่างแยกต่อ variant",
    },
    "linkedin": {
        "program": "LinkedIn",
        "open": "linkedin.com ▸ หน้า Company Page ▸ Admin view ▸ Create ▸ Start a post",
        "placement": "Post as ▸ เลือกเพจบริษัท",
        "text": "กล่องข้อความ What do you want to talk about?",
        "media": "Add media (ไอคอนรูปภาพ) ▸ Upload from computer",
        "draft": "ปิดหน้าต่างโพสต์ (×) ▸ Save as draft",
        "link": "วางลิงก์ติดตามในข้อความ (LinkedIn สร้างพรีวิวลิงก์)",
        "ab": "LinkedIn ไม่มี A/B สำหรับโพสต์ออร์แกนิก: สร้างแบบร่างแยกต่อ variant แล้วให้ผู้อนุมัติเลือกเวลาโพสต์",
    },
    "line": {
        "program": "LINE Official Account Manager",
        "open": "manager.line.biz ▸ เลือกบัญชี ▸ Broadcast messages ▸ Create new",
        "placement": "Recipients: ตั้งไว้ตามที่ผู้อนุมัติกำหนด (เครื่องมือไม่เลือกกลุ่มเป้าหมาย)",
        "text": "Message ▸ Text ▸ กล่องข้อความ",
        "media": "Message ▸ Image / Video ▸ Upload",
        "draft": "Save draft (ห้ามกด Send)",
        "link": "วางลิงก์ติดตามในข้อความ Text",
        "ab": "Broadcast ▸ A/B test (ใช้ได้เมื่อบัญชีมีเพื่อนถึงเกณฑ์ของ LINE); ถ้าไม่ได้ให้สร้างแบบร่างแยกต่อ variant",
    },
    "email": {
        "program": "Mailchimp",
        "open": "Mailchimp ▸ Create ▸ Email ▸ Regular email",
        "placement": "To: ปล่อยว่างไว้ให้ผู้อนุมัติเลือก audience",
        "text": "Content ▸ Edit design ▸ ลาก Text block ลงในเทมเพลต ▸ วางข้อความ",
        "media": "Content ▸ Edit design ▸ ลาก Image block ▸ Upload",
        "draft": "Save and close (แคมเปญอยู่ในสถานะ Draft; ห้ามกด Send / Schedule)",
        "link": "เลือกข้อความปุ่มหรือลิงก์ ▸ Link ▸ Web address ▸ วางลิงก์ติดตาม; ปิด Settings ▸ Tracking ▸ Google Analytics link tracking เพื่อไม่ให้ utm ซ้ำ",
        "ab": "Create ▸ Email ▸ A/B test ▸ Variable: Content ▸ ใส่เนื้อหาแต่ละ variant",
    },
    "web": {
        "program": "WordPress (Block Editor)",
        "open": "wp-admin ▸ Pages ▸ เปิดหน้าที่จะแก้ (หรือ Add New Page)",
        "placement": "ตำแหน่งข้อความตามที่ผู้อนุมัติกำหนด",
        "text": "ปุ่ม + ▸ Paragraph block ▸ วางข้อความ",
        "media": "ปุ่ม + ▸ Image block ▸ Upload",
        "draft": "Save draft (ห้ามกด Publish / Update บนหน้าที่ออนไลน์อยู่)",
        "link": "เลือกข้อความ ▸ Link (Ctrl+K) ▸ วางลิงก์ติดตาม",
        "ab": "WordPress ไม่มี A/B ในตัว: สร้างแบบร่างแยกต่อ variant",
    },
}
# Post-ready frame sizes (px) to prepare in Canva for each channel's image slot.
FRAME_SIZES: dict[str, tuple[int, int, str]] = {
    "facebook": (1080, 1080, "Feed 1:1"),
    "instagram": (1080, 1350, "Feed 4:5"),
    "linkedin": (1200, 627, "Shared image 1.91:1"),
    "line": (1040, 1040, "Rich/Image message 1:1"),
    "email": (1200, 600, "Email header 2:1 (แสดงกว้าง 600 px)"),
    "web": (1200, 630, "Hero/OG image 1.91:1"),
}
_KIND_LABELS = {"copy": "ข้อความ", "email": "ข้อความอีเมล", "image": "ภาพ", "video": "วิดีโอ", "landing_page": "ลิงก์หน้า landing"}


def programs_for(ir: CampaignIR) -> list[str]:
    names: list[str] = []
    for channel in ir.channels:
        name = _PROGRAMS[channel]["program"]
        if name not in names:
            names.append(name)
    if any(asset.kind in {"image", "video"} for asset in ir.assets):
        names.append("Canva")
    return names


def _file_name(asset: AssetSpec) -> str:
    return asset.uri.split("/")[-1]


def export_name(asset: AssetSpec) -> str:
    """File name of the post-ready Canva export for an image/video slot."""

    stem = _file_name(asset).rsplit(".", 1)[0].replace("inline:", "") or asset.asset_id
    return f"{stem}-{asset.channel}.png" if asset.kind == "image" else f"{stem}-{asset.channel}.mp4"


def _asset_steps(guide: ManualGuide, asset: AssetSpec, links: Mapping[str, str], section: str) -> None:
    spec = _PROGRAMS[asset.channel]
    label = f"{_KIND_LABELS[asset.kind]} `{asset.asset_id}`" + (f" (variant {asset.variant})" if asset.variant else "")
    if asset.kind in {"copy", "email"} and asset.text is not None:
        limit = CHANNEL_LIMITS.get(asset.channel)
        tags, mentions = hashtags_in(asset.text), mentions_in(asset.text)
        note = f"{copy_length(asset.text)}/{limit} ตัวอักษร (ขีดจำกัดของเครื่องมือ)"
        if tags or mentions:
            note += f"; hashtag {len(tags)} ({', '.join('#' + tag for tag in tags) or '—'}), mention {len(mentions)}"
        if asset.text[:1] in {"=", "+", "-", "@"}:
            note += "; ขึ้นต้นด้วยเครื่องหมาย ให้วางเป็นข้อความธรรมดา"
        guide.step(f"วาง{label}", tool=spec["text"], value=asset.text, note=note, section=section)
    elif asset.kind in {"image", "video"}:
        inline = asset.uri.lower().startswith("inline:")
        source = "ไฟล์ตัวยึดตำแหน่ง (inline) — ขอไฟล์จริงจากทีมออกแบบ" if inline else _file_name(asset)
        guide.step(f"ใส่{label}", tool=spec["media"], value=export_name(asset), note=f"ไฟล์จากขั้น Canva; ต้นฉบับ: {source}", section=section)
    elif asset.kind == "landing_page":
        guide.step(f"ใส่{label}", tool=spec["link"], value=links.get(asset.asset_id, asset.uri), section=section)
    if asset.kind in {"copy", "email"} and asset.asset_id in links:
        guide.step(f"วางลิงก์ติดตามของ `{asset.asset_id}`", tool=spec["link"], value=links[asset.asset_id], section=section)


def _canva_steps(guide: ManualGuide, assets: Sequence[AssetSpec]) -> None:
    section = "Canva: เตรียมภาพ/วิดีโอขนาดตามช่องทาง"
    for asset in assets:
        width, height, preset = FRAME_SIZES[asset.channel]
        name = _file_name(asset)
        guide.layer(f"{asset.channel}/{asset.asset_id}", "Canva frame", content=f"{width}×{height} px ({preset})", position="Page 1")
        guide.layer(f"{asset.asset_id}: background", "Image layer" if asset.kind == "image" else "Video layer", content=name, position="เต็มเฟรม (Set as background)")
        guide.step("สร้างดีไซน์ขนาดกำหนดเอง", tool="Canva ▸ Create a design ▸ Custom size", value=f"Width {width} px × Height {height} px", note=preset, section=section)
        guide.step("อัปโหลดไฟล์ต้นฉบับ", tool="Uploads ▸ Upload files", value=name, section=section)
        guide.step("ลากไฟล์ลงเฟรม แล้วตั้งเป็นพื้นหลังเต็มเฟรม", tool="คลิกขวาที่ภาพ ▸ Set image as background", target="Page 1", section=section)
        export = "PNG" if asset.kind == "image" else "MP4 Video"
        guide.step("ดาวน์โหลดไฟล์พร้อมโพสต์", tool=f"Share ▸ Download ▸ File type: {export}", value=export_name(asset), section=section)


def build_marketing_guide(ir: CampaignIR, *, links: Sequence[Mapping[str, str]] = (), experiments: Sequence[Mapping[str, Any]] = (), checks: Sequence[Mapping[str, str]] = ()) -> ManualGuide:
    """Translate the packaged CampaignIR into per-channel, click-level draft steps."""

    names = programs_for(ir)
    link_by_asset = {item["asset_id"]: item["url"] for item in links}
    guide = ManualGuide(
        program=" + ".join(names),
        alternatives=["Hootsuite / Buffer (สร้างแบบร่างหลายช่องทางพร้อมกัน)", "Google Campaign URL Builder (สร้างลิงก์ utm)"],
        title=ir.name,
        summary=(
            "ทำตามขั้นตอนนี้จะได้ 'แบบร่าง' ของแคมเปญเดียวกับแพ็กเกจที่เครื่องมือสร้าง แยกตามช่องทาง; "
            "V1A เป็นแบบ review-only — ห้ามกด Publish / Schedule / Send / Boost และไม่ตั้งงบหรือ audience ในโปรแกรม"
        ),
        setup=[
            f"เปิดแพ็กเกจ `.campaign.json` ของเครื่องมือไว้เทียบ (campaign_id `{ir.campaign_id}`, objective `{ir.objective}`)",
            f"ล็อกอินโปรแกรมของแต่ละช่องทาง: {', '.join(names)}",
            f"กลุ่มเป้าหมาย '{ir.audience.name}': " + "; ".join(f"{rule.field} {rule.operator} {rule.value}" for rule in ir.audience.rules)
            + " — ใช้เป็นบันทึกให้ผู้อนุมัติเท่านั้น (V1A ไม่ตั้ง audience/งบ)",
        ],
    )
    images = [asset for asset in ir.assets if asset.kind in {"image", "video"}]
    if images:
        _canva_steps(guide, images)
    for channel in ir.channels:
        spec = _PROGRAMS[channel]
        assets = [asset for asset in ir.assets if asset.channel == channel]
        section = f"{channel}: {spec['program']}"
        guide.layer(f"{channel} draft", spec["program"], content=", ".join(f"{asset.kind}:{asset.asset_id}" for asset in assets), position=spec["text"])
        group = next((item for item in experiments if item["channel"] == channel), None)
        guide.step("เปิดหน้าสร้างโพสต์/ข้อความ", tool=spec["open"], section=section)
        guide.step("เลือกปลายทาง", tool=spec["placement"], section=section)
        if group is not None:
            versions = ", ".join(f"{item['variant']}={item['asset_id']}" for item in group["variants"])
            guide.step("ตั้ง A/B test", tool=spec["ab"], value=versions, section=section)
        for asset in assets:
            _asset_steps(guide, asset, link_by_asset, section)
        for finding in (item for item in checks if item["channel"] == channel):
            guide.step("ตรวจข้อสังเกตจากเครื่องมือก่อนส่งอนุมัติ", target=f"asset {finding['asset_id']}", value=finding["code"], note=f"ระดับ {finding['severity']}", section=section)
        guide.step("บันทึกเป็นแบบร่างแล้วส่งให้ผู้อนุมัติ (ห้ามโพสต์/ตั้งเวลาเอง)", tool=spec["draft"], section=section)

    text_assets = [asset for asset in ir.assets if asset.text]
    guide.checks = [
        f"มีแบบร่างครบ {len(ir.channels)} ช่องทาง: {', '.join(ir.channels)}",
        *(f"`{asset.asset_id}` ({asset.channel}) ยาว {copy_length(asset.text or '')} ตัวอักษร ≤ {CHANNEL_LIMITS.get(asset.channel)}; ข้อความตรงกับแพ็กเกจทุกตัวอักษร" for asset in text_assets),
        *(f"ลิงก์ของ `{item['asset_id']}` = `{item['url']}` (package ▸ links)" for item in links),
        *(f"ไฟล์ต้นฉบับ `{asset.uri}` มี sha256 ตรงกับ package ▸ lineage.asset_hashes.{asset.asset_id} (`sha256sum`)" for asset in images if not asset.uri.lower().startswith("inline:")),
        "ไม่มีโพสต์ใดถูก Publish / Schedule / Send และไม่มีงบโฆษณาถูกตั้ง",
    ]
    guide.glossary = {
        "แบบร่าง (draft)": "สถานะที่ยังไม่เผยแพร่; ผู้อนุมัติเป็นคนกดเผยแพร่เอง",
        "utm": "พารามิเตอร์ท้ายลิงก์ที่บอก Google Analytics ว่าคลิกมาจากช่องทาง/แคมเปญ/ชิ้นงานใด",
        "A/B test": "ทดลองหลายเวอร์ชันของชิ้นงานเดียวกันเพื่อดูว่าแบบไหนได้ผลกว่า",
    }
    guide.estimated_minutes = round(
        3 + 3 * len(text_assets) + 8 * len(images) + 1 * len(links) + 4 * len(experiments) + 2 * len(ir.channels), 1
    )
    return guide


__all__ = ["FRAME_SIZES", "build_marketing_guide", "export_name", "programs_for"]
