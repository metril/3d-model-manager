import { useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";

import { useLogin, useOidcProviders } from "@/api/auth";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const ERROR_COPY: Record<string, string> = {
  not_provisioned: "Your account isn't set up here yet. Ask an admin to add you.",
  inactive: "This account has been deactivated.",
  invalid_state: "That sign-in link expired. Please try again.",
  oidc_failed: "Single sign-on failed. Try again or use your password.",
};

export function LoginPage() {
  const { error: errorCode } = useSearch({ strict: false }) as { error?: string };
  const providers = useOidcProviders().data ?? [];
  const bannerMessage = errorCode ? (ERROR_COPY[errorCode] ?? ERROR_COPY.oidc_failed) : null;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const login = useLogin();

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    login.mutate(
      { username, password },
      { onSuccess: () => void navigate({ to: "/" }) },
    );
  }

  const errorMessage =
    login.error instanceof ApiError ? login.error.detail : login.error ? "Login failed" : null;

  return (
    <div className="flex min-h-svh items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>3D Model Manager</CardTitle>
          <CardDescription>Sign in to continue</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
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
        </CardContent>
      </Card>
    </div>
  );
}
