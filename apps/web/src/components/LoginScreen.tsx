import { useEffect, useId, useRef, useState, type SubmitEvent } from "react";

import { InvalidCredentialsError } from "../api/client";
import { useAuth } from "../auth/context";
import { describeError } from "../auth/messages";
import { LogoMark } from "./LogoMark";
import { PasswordField } from "./PasswordField";

export const ETHICS_URL = "https://github.com/TheRealNightmare/nightloom/blob/main/ETHICS.md";

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
    <main className="login">
      <section className="login__card" aria-labelledby={titleId}>
        <LogoMark size={44} />
        <h1 id={titleId} className="login__title">
          Sign in to Nightloom
        </h1>
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
      </section>
      <p className="login__footer">
        <a href={ETHICS_URL} target="_blank" rel="noreferrer">
          Use Nightloom responsibly
        </a>
      </p>
    </main>
  );
}
