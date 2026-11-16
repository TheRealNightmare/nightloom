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
 * reports that the session has ended.
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
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ state, login, logout, changePassword }),
    [state, login, logout, changePassword],
  );
  return <AuthContext value={value}>{children}</AuthContext>;
}
