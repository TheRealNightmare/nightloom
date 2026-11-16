import { createContext, useContext } from "react";

import type { User } from "../api/client";

export const SESSION_ENDED_NOTICE = "Your session has ended. Please sign in again.";

export type AuthState =
  | { status: "loading" }
  | { status: "signed_out"; notice: string | null }
  | { status: "signed_in"; user: User };

export interface AuthContextValue {
  state: AuthState;
  /** Signs in; rejects with the client's typed errors (the state stays signed out). */
  login: (email: string, password: string) => Promise<void>;
  /** Signs out on the server, then locally, even when the server call fails. */
  logout: () => Promise<void>;
  /** Rejects with the client's typed errors; stays signed in on success. */
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth() must be used inside <AuthProvider>");
  return value;
}
