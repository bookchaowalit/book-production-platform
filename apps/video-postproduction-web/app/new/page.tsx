import Link from "next/link";

import {ProjectCreateForm} from "@/components/project-create-form";

export default function NewProjectPage() {
  return (
    <div className="page-frame narrow-frame">
      <header className="topbar">
        <Link className="brand" href="/">POST / STUDIO</Link>
        <span className="mode-label">NEW PROJECT</span>
      </header>
      <div className="page-heading compact-heading">
        <div>
          <Link className="back-link" href="/">← Projects</Link>
          <p className="eyebrow">Input boundary</p>
          <h1>เริ่ม post-production job</h1>
          <p className="lede">บอก context ให้ engine แล้วส่งวิดีโอต้นฉบับเข้า local pipeline</p>
        </div>
      </div>
      <ProjectCreateForm />
    </div>
  );
}
