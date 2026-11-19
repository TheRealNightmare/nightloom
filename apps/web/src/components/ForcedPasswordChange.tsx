import { useId, useState } from "react";

import type { User } from "../api/client";
import { useAuth } from "../auth/context";
import { LogoMark } from "./LogoMark";
import { PasswordChangeForm } from "./PasswordChangeForm";

export const FORCED_CHANGE_TITLE = "Choose a new password";
export const FORCED_CHANGE_TEXT =
  "You signed in with a temporary password. Choose a new password of your own to continue.";

export interface ForcedPasswordChangeProps {
  user: User;
}

/**
 * Full-page screen for a user who must replace a temporary password. It
 * cannot be dismissed: the only ways out are a successful change (the app
 * then shows the page that was asked for, since the URL never changed) and
 * signing out.
 */
export function ForcedPasswordChange({ user }: ForcedPasswordChangeProps) {
  const { logout } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const titleId = useId();

  return (
    <main className="login">
      <section className="login__card" aria-labelledby={titleId}>
        <LogoMark size={44} />
        <h1 id={titleId} className="login__title">
          {FORCED_CHANGE_TITLE}
        </h1>
        <p className="login__intro">{FORCED_CHANGE_TEXT}</p>
        <p className="login__account">
          Signed in as <strong>{user.email}</strong>
        </p>
        <div className="login__form">
          <PasswordChangeForm
            // On success the auth state clears the flag and this screen unmounts.
            onSuccess={() => undefined}
            currentLabel="Temporary password"
            submitLabel="Set new password"
            actionsClassName="login__actions"
            secondary={
              <button
                type="button"
                className="button"
                disabled={signingOut}
                onClick={() => {
                  setSigningOut(true);
                  void logout();
                }}
              >
                Sign out
              </button>
            }
          />
        </div>
      </section>
    </main>
  );
}
