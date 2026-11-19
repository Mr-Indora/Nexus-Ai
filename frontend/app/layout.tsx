import type { Metadata } from "next";
import "./globals.css";
import { NexusProvider } from "@/lib/store";

export const metadata: Metadata = {
  title: "Nexus AI — Personal Intelligence Workspace",
  description:
    "Nexus AI brings messages, college notices, Classroom updates and calendar events together with evidence-backed tasks and recommendations.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <NexusProvider>{children}</NexusProvider>
      </body>
    </html>
  );
}
