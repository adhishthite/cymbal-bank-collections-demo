import type { Metadata } from "next";

// page.tsx is a client component, so the route title lives here.
export const metadata: Metadata = { title: "Collections Desk" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
