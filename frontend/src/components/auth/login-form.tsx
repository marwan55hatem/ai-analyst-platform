"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Field } from "@/components/common/field";
import { useErrorMessage } from "@/components/common/error-state";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-provider";

import { PasswordInput as PasswordField } from "./password-input";

export function LoginForm() {
  const t = useTranslations("auth");
  const { login } = useAuth();
  const params = useSearchParams();
  const errorMessage = useErrorMessage();
  const [formError, setFormError] = useState<string | null>(null);

  const schema = z.object({
    email: z
      .string()
      .trim()
      .min(1, t("validation.required", { field: t("fieldNames.email") }))
      .pipe(z.email(t("validation.email"))),
    password: z.string().min(1, t("validation.required", { field: t("fieldNames.password") })),
  });
  type Values = z.infer<typeof schema>;

  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { email: "", password: "" } });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async (values) => {
    setFormError(null);
    try {
      await login(values.email, values.password);
    } catch (error) {
      setFormError(
        error instanceof ApiError && error.status === 401 ? t("badCredentials") : errorMessage(error),
      );
    }
  });

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">{t("loginTitle")}</h1>
        <p className="text-muted-foreground">{t("loginSubtitle")}</p>
      </div>
      {params.get("expired") ? (
        <Alert>
          <AlertDescription>{t("expired")}</AlertDescription>
        </Alert>
      ) : null}
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        <Field label={t("email")} error={errors.email?.message}>
          <Input type="email" autoComplete="email" dir="ltr" {...form.register("email")} />
        </Field>
        <Field label={t("password")} error={errors.password?.message}>
          <PasswordField autoComplete="current-password" {...form.register("password")} />
        </Field>
        {formError ? (
          <p role="alert" className="rounded-md bg-bad-soft px-3 py-2 text-sm font-medium text-bad">
            {formError}
          </p>
        ) : null}
        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? t("loginBusy") : t("loginSubmit")}
        </Button>
      </form>
      <p className="text-sm text-muted-foreground">
        {t("noAccount")}{" "}
        <Link href="/register" className="font-medium text-primary underline underline-offset-4">
          {t("toRegister")}
        </Link>
      </p>
    </div>
  );
}
