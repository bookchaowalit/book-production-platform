import Link from "next/link";

import {ProjectEditor} from "@/components/project-editor";

interface ProjectPageProps {
  params: Promise<{projectId: string}>;
  searchParams: Promise<{assetId?: string; jobId?: string}>;
}

export default async function ProjectPage({params, searchParams}: ProjectPageProps) {
  const {projectId} = await params;
  const query = await searchParams;
  return (
    <div className="page-frame">
      <header className="topbar">
        <Link className="brand" href="/">POST / STUDIO</Link>
        <span className="mode-label">PROJECT EDITOR · V1A</span>
      </header>
      <ProjectEditor projectId={projectId} assetId={query.assetId} jobId={query.jobId} />
    </div>
  );
}
