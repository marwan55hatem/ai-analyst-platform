"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Field } from "@/components/common/field";
import { useErrorMessage } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth/auth-provider";
import { passwordProblems } from "@/lib/errors";

import { PasswordInput } from "./password-input";

/** Map the backend's English policy phrases to message keys under auth.problems. */
export function problemKey(problem: string): string {
  if (problem.includes("at least")) return "short";
  if (problem.includes("at most")) return "long";
  if (problem.includes("common")) return "common";
  if (problem.includes("repetitive")) return "repetitive";
  if (problem.includes("email")) return "email";
  if (problem.includes("name")) return "name";
  return "other";
}

/** Same checks as backend/app/core/password_policy.py that need no word list. */
export function localPasswordIssue(
  password: string,
  email: string,
  fullName: string,
): "passwordShort" | "passwordLong" | "passwordHasEmail" | "passwordHasName" | null {
  const lowered = password.toLowerCase();
  if (password.length < 10) return "passwordShort";
  if (password.length > 128) return "passwordLong";
  const local = (email.split("@")[0] ?? "").toLowerCase();
  if (local.length >= 4 && lowered.includes(local)) return "passwordHasEmail";
  if (fullName.replace(/ /g, "").length >= 4 && lowered.includes(fullName.toLowerCase())) {
    return "passwordHasName";
  }
  return null;
}

export function RegisterForm() {
  const t = useTranslations("auth");
  const { register: createAccount } = useAuth();
  const errorMessage = useErrorMessage();
  const [formError, setFormError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);

  const required = (field: string) => t("validation.required", { field: t(`fieldNames.${field}`) });
  const schema = z
    .object({
      company_name: z.string().trim().min(1, required("company")).max(200, t("validation.tooLong")),
      full_name: z.string().trim().min(1, required("fullName")).max(200, t("validation.tooLong")),
      email: z.string().trim().min(1, required("email")).pipe(z.email(t("validation.email"))),
      password: z.string().min(1, required("password")),
    })
    .superRefine((v, ctx) => {
      const issue = localPasswordIssue(v.password, v.email, v.full_name);
      if (issue && v.password) {
        ctx.addIssue({ code: "custom", path: ["password"], message: t(`validation.${issue}`) });
      }
    });
  type Values = z.infer<typeof schema>;

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { company_name: "", full_name: "", email: "", password: "" },
  });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async (values) => {
    setFormError(null);
    setProblems([]);
    try {
      await createAccount(values);
    } catch (error) {
      const listed = passwordProblems(error);
      if (listed.length) {
        setProblems(listed.map((p) => t(`problems.${problemKey(p)}`)));
        form.setFocus("password");
      } else {
        setFormError(errorMessage(error));
      }
    }
  });

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">{t("registerTitle")}</h1>
        <p className="text-muted-foreground">{t("registerSubtitle")}</p>
      </div>
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        <Field label={t("company")} error={errors.company_name?.message}>
          <Input autoComplete="organization" dir="auto" {...form.register("company_name")} />
        </Field>
        <Field label={t("fullName")} error={errors.full_name?.message}>
          <Input autoComplete="name" dir="auto" {...form.register("full_name")} />
        </Field>
        <Field label={t("email")} error={errors.email?.message}>
          <Input type="email" autoComplete="email" dir="ltr" {...form.register("email")} />
        </Field>
        <Field
          label={t("password")}
          hint={t("passwordHint")}
          error={
            errors.password?.message ??
            (problems.length ? (
              <>
                {t("passwordProblems")}
                <ul className="mt-1 list-disc ps-5">
                  {problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              </>
            ) : undefined)
          }
        >
          <PasswordInput autoComplete="new-password" {...form.register("password")} />
        </Field>
        {formError ? (
          <p role="alert" className="rounded-md bg-bad-soft px-3 py-2 text-sm font-medium text-bad">
            {formError}
          </p>
        ) : null}
        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? t("registerBusy") : t("registerSubmit")}
        </Button>
      </form>
      <p className="text-sm text-muted-foreground">
        {t("haveAccount")}{" "}
        <Link href="/login" className="font-medium text-primary underline underline-offset-4">
          {t("toLogin")}
        </Link>
      </p>
    </div>
  );
}
