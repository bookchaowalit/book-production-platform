import Link from "next/link";

import {ProjectList} from "@/components/project-list";

export default function ProjectsPage() {
  return (
    <div className="page-frame">
      <header className="topbar">
        <Link className="brand" href="/">POST / STUDIO</Link>
        <span className="mode-label">LOCAL-FIRST ENGINE · V1A</span>
      </header>
      <section className="page-heading">
        <div>
          <p className="eyebrow">Workspace</p>
          <h1>Projects</h1>
          <p className="lede">จัดการ source video, transcript และ edit decision list ในที่เดียว</p>
        </div>
        <Link className="button button-primary" href="/new">+ New project</Link>
      </section>
      <ProjectList />
    </div>
  );
}
