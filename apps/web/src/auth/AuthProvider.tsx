import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import * as client from "../api/client";
import {
  AuthContext,
  SESSION_ENDED_NOTICE,
  type AuthContextValue,
  type AuthState,
} from "./context";

export interface AuthProviderProps {
  children: ReactNode;
}

/**
 * Holds who is signed in. Asks `GET /auth/me` on start (loading → signed in
 * or signed out) and signs out locally, with a notice, whenever any request
 * reports that the session has ended. A request refused with
 * `password_change_required` marks the user as having to change their
 * password, which {@link AuthGate} turns into the forced-change screen; a
 * successful change clears it.
 */
export function AuthProvider({ children }: AuthProviderProps) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    client.me({ signal: controller.signal, notifySessionExpired: false }).then(
      (user) => {
        setState({ status: "signed_in", user });
      },
      (error: unknown) => {
        if (error instanceof client.RequestAbortedError) return;
        // No session, an unreachable API or an odd answer: show the sign-in form.
        setState({ status: "signed_out", notice: null });
      },
    );
    return () => {
      controller.abort();
    };
  }, []);

  useEffect(
    () =>
      client.onSessionExpired(() => {
        setState((current) =>
          current.status === "signed_in"
            ? { status: "signed_out", notice: SESSION_ENDED_NOTICE }
            : current,
        );
      }),
    [],
  );

  useEffect(
    () =>
      client.onPasswordChangeRequired(() => {
        setState((current) => withPasswordChangeRequired(current, true));
      }),
    [],
  );

  const login = useCallback(async (email: string, password: string) => {
    const user = await client.login(email, password);
    setState({ status: "signed_in", user });
  }, []);

  const logout = useCallback(async () => {
    try {
      await client.logout({ notifySessionExpired: false });
    } catch {
      // Signed out locally regardless; the server session expires on its own.
    } finally {
      setState({ status: "signed_out", notice: null });
    }
  }, []);

  const changePassword = useCallback(async (currentPassword: string, newPassword: string) => {
    await client.changePassword(currentPassword, newPassword);
    setState((current) => withPasswordChangeRequired(current, false));
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ state, login, logout, changePassword }),
    [state, login, logout, changePassword],
  );
  return <AuthContext value={value}>{children}</AuthContext>;
}

function withPasswordChangeRequired(state: AuthState, required: boolean): AuthState {
  if (state.status !== "signed_in" || state.user.must_change_password === required) return state;
  return { status: "signed_in", user: { ...state.user, must_change_password: required } };
}
