import { AppShell } from "../app/AppShell";
import { LoginScreen } from "../components/LoginScreen";
import { Splash } from "../components/Splash";
import { useAuth } from "./context";

/**
 * The root route's element: a splash while loading, the sign-in screen when
 * signed out, else the app. The URL is left alone while signed out, so after
 * signing in the user lands on the page they asked for.
 */
export function AuthGate() {
  const { state } = useAuth();
  switch (state.status) {
    case "loading":
      return <Splash />;
    case "signed_out":
      return <LoginScreen notice={state.notice} />;
    case "signed_in":
      return <AppShell user={state.user} />;
  }
}
