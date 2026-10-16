import type { Metadata } from "next";
import {
  Anek_Devanagari,
  Anek_Kannada,
  Anek_Latin,
  Anek_Malayalam,
  Anek_Tamil,
  Anek_Telugu,
} from "next/font/google";

// page.tsx is a client component, so the route title lives here.
export const metadata: Metadata = { title: "Live Call Monitor" };

// One type family in every call language, so a Hindi or Malayalam caption matches the English UI.
// Each script is a separate font file. The browser loads a file only when a caption uses that script.
const latin = Anek_Latin({ subsets: ["latin"], axes: ["wdth"], variable: "--f-latin" });
const deva = Anek_Devanagari({ subsets: ["devanagari"], axes: ["wdth"], variable: "--f-deva" });
const mal = Anek_Malayalam({ subsets: ["malayalam"], axes: ["wdth"], variable: "--f-mal" });
const kan = Anek_Kannada({ subsets: ["kannada"], axes: ["wdth"], variable: "--f-kan" });
const tam = Anek_Tamil({ subsets: ["tamil"], axes: ["wdth"], variable: "--f-tam" });
const tel = Anek_Telugu({ subsets: ["telugu"], axes: ["wdth"], variable: "--f-tel" });

export default function Layout({ children }: { children: React.ReactNode }) {
  const fonts = [latin, deva, mal, kan, tam, tel].map((f) => f.variable).join(" ");
  return <div className={`monitor-fonts ${fonts}`}>{children}</div>;
}
