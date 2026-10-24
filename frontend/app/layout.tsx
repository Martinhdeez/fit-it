import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Fit-it",
  description: "Describe a change to this app.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
