import { useEffect, useId, useRef, useState, type SubmitEvent } from "react";

import { InvalidCredentialsError } from "../api/client";
import { useAuth } from "../auth/context";
import { describeError } from "../auth/messages";
import { ThemeToggle } from "../theme/ThemeToggle";
import { APP_VERSION } from "../version";
import { LoomArt } from "./LoomArt";
import { LogoMark } from "./LogoMark";
import { PasswordField } from "./PasswordField";

export const ETHICS_URL = "https://github.com/TheRealNightmare/nightloom/blob/main/ETHICS.md";
export const TAGLINE = "Every thread, woven into the full picture.";

export interface LoginScreenProps {
  /** Shown above the form, e.g. after the session ended. */
  notice?: string | null;
}

export function LoginScreen({ notice = null }: LoginScreenProps) {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const mounted = useRef(true);
  const emailId = useId();
  const titleId = useId();

  useEffect(() => {
    mounted.current = true;
    emailRef.current?.focus();
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (error !== null) errorRef.current?.focus();
  }, [error]);

  const onSubmit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    if (email.trim() === "" || password === "") {
      setError("Enter your email and password.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await login(email.trim(), password);
      // Signed in: this screen unmounts.
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof InvalidCredentialsError) setPassword("");
      setError(describeError(caught));
      setPending(false);
    }
  };

  return (
    <main className="login login--split">
      <div className="login__art">
        <LoomArt />
        <p className="login__tagline">{TAGLINE}</p>
        <p className="login__caption">Entities are knots · relationships are threads</p>
      </div>
      <section className="login__panel" aria-labelledby={titleId}>
        <div className="login__panel-top">
          <LogoMark size={40} />
          <ThemeToggle />
        </div>
        <h1 id={titleId} className="login__title">
          {/* Read as "Sign in to Nightloom"; the space sits outside the span so it survives. */}
          Sign in <span className="visually-hidden">to Nightloom</span>
        </h1>
        <p className="login__subtitle">Open-source intelligence, mapped as a graph.</p>
        {notice !== null && (
          <p className="login__notice" role="status">
            {notice}
          </p>
        )}
        <form
          className="login__form"
          noValidate
          aria-busy={pending}
          onSubmit={(event) => {
            void onSubmit(event);
          }}
        >
          <div className="field">
            <label className="field__label" htmlFor={emailId}>
              Email
            </label>
            <input
              ref={emailRef}
              id={emailId}
              name="email"
              className="field__input"
              type="email"
              inputMode="email"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
              }}
            />
          </div>
          <PasswordField
            label="Password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={setPassword}
          />
          <div
            ref={errorRef}
            className="form-error"
            aria-live="polite"
            tabIndex={-1}
            data-testid="login-error"
          >
            {error}
          </div>
          <button type="submit" className="button button--primary login__submit" disabled={pending}>
            {pending ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className="login__footer">
          Use Nightloom responsibly ·{" "}
          <a href={ETHICS_URL} target="_blank" rel="noreferrer">
            Ethics policy
          </a>{" "}
          · <span className="login__version">v{APP_VERSION}</span>
        </p>
      </section>
    </main>
  );
}
