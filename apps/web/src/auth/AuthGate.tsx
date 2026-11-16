import { App, type AppProps } from "../App";
import { LoginScreen } from "../components/LoginScreen";
import { Splash } from "../components/Splash";
import { UserMenu } from "../components/UserMenu";
import { useAuth } from "./context";

export type AuthGateProps = Omit<AppProps, "account">;

/** Splash while loading, the sign-in screen when signed out, else the app. */
export function AuthGate(appProps: AuthGateProps) {
  const { state } = useAuth();
  switch (state.status) {
    case "loading":
      return <Splash />;
    case "signed_out":
      return <LoginScreen notice={state.notice} />;
    case "signed_in":
      return <App {...appProps} account={<UserMenu user={state.user} />} />;
  }
}
