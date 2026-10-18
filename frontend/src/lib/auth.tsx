/** Sign in with Google (Firebase Auth, loaded from Google's CDN only when the server enables it) and the account state. */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { errorText, get, setAuthToken } from "./api";

const FIREBASE = "https://www.gstatic.com/firebasejs/11.0.2";

type AuthConfig = { enabled: boolean; apiKey: string; authDomain: string; projectId: string; appId: string };
export type Step = { step_id: string; title: string; detail: string; link: string | null; done: boolean };
export type Account = {
  status: "unregistered" | "pending" | "approved" | "rejected";
  uid?: string;
  email: string;
  name: string;
  factory_name?: string;
  industry?: string;
  role?: string;
  city?: string | null;
  plant_scale?: string | null;
  created_at?: string;
  decided_by?: string | null;
  note?: string | null;
  template_id?: string | null;
  is_admin: boolean;
  industries?: string[];
  roles?: string[];
  onboarding?: { steps: Step[]; done: number; total: number; template: string | null };
};
type User = { email: string; name: string; photo: string | null };

type Ctx = {
  enabled: boolean;
  ready: boolean;
  user: User | null;
  account: Account | null;
  error: string | null;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
  setAccount: (a: Account) => void;
};

const AuthCtx = createContext<Ctx | null>(null);

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabled] = useState(false);
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fb, setFb] = useState<{ auth: Any; mod: Any } | null>(null);

  const refresh = useCallback(async () => {
    try {
      setAccount(await get<Account>("/api/account/me"));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    let unsub: (() => void) | undefined;
    (async () => {
      try {
        const cfg = await get<AuthConfig>("/api/auth/config");
        if (!cfg.enabled) return;
        const app = await import(/* @vite-ignore */ `${FIREBASE}/firebase-app.js`);
        const mod = await import(/* @vite-ignore */ `${FIREBASE}/firebase-auth.js`);
        const auth = mod.getAuth(app.initializeApp({ apiKey: cfg.apiKey, authDomain: cfg.authDomain, projectId: cfg.projectId, appId: cfg.appId }));
        setFb({ auth, mod });
        setEnabled(true);
        unsub = mod.onIdTokenChanged(auth, async (u: Any) => {
          if (!u) {
            setAuthToken(null);
            setUser(null);
            setAccount(null);
            return;
          }
          setAuthToken(await u.getIdToken());
          setUser({ email: u.email, name: u.displayName ?? u.email, photo: u.photoURL });
          try {
            setAccount(await get<Account>("/api/account/me"));
          } catch (e) {
            setError(errorText(e));
          }
        });
      } catch (e) {
        setError(`Sign-in is unavailable: ${errorText(e)}`);
      } finally {
        setReady(true);
      }
    })();
    return () => unsub?.();
  }, []);

  const signIn = useCallback(async () => {
    if (!fb) return;
    setError(null);
    try {
      await fb.mod.signInWithPopup(fb.auth, new fb.mod.GoogleAuthProvider());
    } catch (e) {
      setError(errorText(e));
    }
  }, [fb]);

  const signOut = useCallback(async () => {
    if (fb) await fb.mod.signOut(fb.auth);
  }, [fb]);

  const value = useMemo(() => ({ enabled, ready, user, account, error, signIn, signOut, refresh, setAccount }), [enabled, ready, user, account, error, signIn, signOut, refresh]);
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): Ctx {
  const c = useContext(AuthCtx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}
