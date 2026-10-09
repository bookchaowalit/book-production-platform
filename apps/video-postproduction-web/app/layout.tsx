import type {Metadata} from "next";
import type {PropsWithChildren} from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Post-production studio",
  description: "Local-first AI video post-production control surface",
};

export default function RootLayout({children}: PropsWithChildren) {
  return (
    <html lang="th">
      <body>
        <main className="app-shell">{children}</main>
      </body>
    </html>
  );
}
