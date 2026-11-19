import { AppShell } from "../app/AppShell";
import { ForcedPasswordChange } from "../components/ForcedPasswordChange";
import { LoginScreen } from "../components/LoginScreen";
import { Splash } from "../components/Splash";
import { useAuth } from "./context";

/**
 * The root route's element: a splash while loading, the sign-in screen when
 * signed out, the "Choose a new password" screen while a temporary password
 * must be replaced, else the app. The URL is left alone on both screens, so
 * afterwards the user lands on the page they asked for.
 */
export function AuthGate() {
  const { state } = useAuth();
  switch (state.status) {
    case "loading":
      return <Splash />;
    case "signed_out":
      return <LoginScreen notice={state.notice} />;
    case "signed_in":
      return state.user.must_change_password ? (
        <ForcedPasswordChange user={state.user} />
      ) : (
        <AppShell user={state.user} />
      );
  }
}
