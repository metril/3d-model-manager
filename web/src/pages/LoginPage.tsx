import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearch } from "@tanstack/react-router";

import { useLogin, useOidcProviders, type TwoFactorMethod } from "@/api/auth";
import { ApiError } from "@/api/client";
import { TwoFactorStep } from "@/components/auth/TwoFactorStep";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const ERROR_COPY: Record<string, string> = {
  not_provisioned: "Your account isn't set up here yet. Ask an admin to add you.",
  inactive: "This account has been deactivated.",
  invalid_state: "That sign-in link expired. Please try again.",
  oidc_failed: "Single sign-on failed. Try again or use your password.",
};

const ALL_METHODS: TwoFactorMethod[] = ["totp", "email", "backup"];

function parseMethods(csv?: string): TwoFactorMethod[] {
  const list = (csv ?? "").split(",").filter((m): m is TwoFactorMethod => ALL_METHODS.includes(m as TwoFactorMethod));
  return list;
}

/** Only same-origin absolute paths; anything else falls back to `/`. */
function safeReturn(to?: string | null): string {
  return to && to.startsWith("/") && !to.startsWith("//") ? to : "/";
}

interface Pending {
  token: string;
  methods: TwoFactorMethod[];
}

export function LoginPage() {
  const { t } = useTranslation();
  const search = useSearch({ strict: false }) as { error?: string; step?: string; token?: string; methods?: string };
  const errorCode = search.error;
  // The OIDC redirect carries the token in the URL fragment (keeps it out of proxy logs);
  // `?step=2fa` query params are still honoured as a fallback. Read once, strip at once.
  const [oidc] = useState(() => {
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    if (hash.get("step")) {
      window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
      return { step: hash.get("step") ?? undefined, token: hash.get("token") ?? undefined, methods: hash.get("methods") ?? undefined };
    }
    return { step: search.step, token: search.token, methods: search.methods };
  });
  const { step, token: oidcToken, methods: oidcMethods } = oidc;
  // OIDC redirect (`?step=2fa&token=`): the token IS the pre-auth token.
  const [pending, setPending] = useState<Pending | null>(() =>
    step === "2fa" && oidcToken && parseMethods(oidcMethods).length
      ? { token: oidcToken, methods: parseMethods(oidcMethods) }
      : null,
  );
  const [notice, setNotice] = useState<string | null>(() =>
    step === "2fa" && (!oidcToken || !parseMethods(oidcMethods).length) ? t("twofa.login.expired") : null,
  );
  const providers = useOidcProviders().data ?? [];
  const bannerMessage = errorCode ? (ERROR_COPY[errorCode] ?? ERROR_COPY.oidc_failed) : null;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const login = useLogin();

  // Keep the one-time token out of the address bar / history.
  useEffect(() => {
    if (step === "2fa" && search.step === "2fa") void navigate({ to: "/login", search: {} as never, replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice(null);
    login.mutate(
      { username, password },
      {
        onSuccess: (res) => {
          if (res.requires_2fa) {
            setPassword("");
            setPending({ token: res.pre_auth_token, methods: res.methods });
          } else void navigate({ to: "/" });
        },
      },
    );
  }

  const errorMessage =
    login.error instanceof ApiError ? login.error.detail : login.error ? "Login failed" : null;

  return (
    <div className="flex min-h-svh items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>3D Model Manager</CardTitle>
          <CardDescription>{pending ? t("twofa.login.title") : "Sign in to continue"}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {pending ? (
            <TwoFactorStep
              token={pending.token}
              methods={pending.methods}
              onDone={(me) => void navigate({ to: safeReturn(me.return_to) as never })}
              onExpired={() => {
                setPending(null);
                setNotice(t("twofa.login.expired"));
              }}
              onBack={() => setPending(null)}
            />
          ) : (
            <>
          {notice ? (
            <p role="alert" className="text-sm text-destructive">
              {notice}
            </p>
          ) : null}
          {bannerMessage ? (
            <p role="alert" className="text-sm text-destructive">
              {bannerMessage}
            </p>
          ) : null}
          {providers.map((provider) => (
            <Button key={provider.slug} asChild variant="outline">
              <a href={`/api/auth/oidc/${provider.slug}/start`}>Sign in with {provider.name}</a>
            </Button>
          ))}
          {providers.length > 0 ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="h-px flex-1 bg-border" />
              or
              <span className="h-px flex-1 bg-border" />
            </div>
          ) : null}
          <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="username" className="text-sm font-medium">
                Username
              </label>
              <Input
                id="username"
                name="username"
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="password" className="text-sm font-medium">
                Password
              </label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </div>
            {errorMessage ? (
              <p role="alert" className="text-sm text-destructive">
                {errorMessage}
              </p>
            ) : null}
            <Button type="submit" disabled={login.isPending} className="mt-2">
              {login.isPending ? "Signing in..." : "Sign in"}
            </Button>
          </form>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
