import { Suspense, type ReactNode } from "react";

import { AuthLayout } from "@/components/auth/auth-layout";

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <Suspense>
      <AuthLayout>{children}</AuthLayout>
    </Suspense>
  );
}
