import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Production Console · Solo Empire',
  description: 'คลังไฟล์งานผลิต ที่มาของไฟล์ การส่งต่อข้ามเครื่องมือ การตรวจงาน และแพ็กเกจส่งมอบ',
};

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="th"><body>{children}</body></html>;
}
