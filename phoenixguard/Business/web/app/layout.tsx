import type { Metadata } from "next";
import type { ReactNode } from "react";
import { RiskDisclosureModal } from "@/components/RiskDisclosureModal";
import "./globals.css";

export const metadata: Metadata = {
  title: "808Fx Standard Hybrid System | PhoenixGuard Engine",
  description:
    "Commercial onboarding, payment, license, and tracker access portal for the 808Fx Standard Hybrid System powered by the PhoenixGuard Engine."
};

export default function RootLayout({
  children
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html lang="en" className="phoenix-command" data-theme="phoenix-command">
      <head>
        <link rel="stylesheet" href="/theme/phoenix-command" />
      </head>
      <body>
        {children}
        <RiskDisclosureModal />
      </body>
    </html>
  );
}
