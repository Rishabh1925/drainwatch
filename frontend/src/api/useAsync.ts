import { useEffect, useState } from "react";

// Minimal async-data hook: run a fetch, expose { data, error, loading, reload }.
// Keeps every page's loading and error handling identical instead of each reinventing
// it, and cancels a stale response if the component unmounts or the inputs change.
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({
    data: null,
    error: null,
    loading: true,
  });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setState((previous) => ({ ...previous, loading: true }));
    fn()
      .then((data) => active && setState({ data, error: null, loading: false }))
      .catch((error) =>
        active &&
        setState({ data: null, error: error instanceof Error ? error.message : "Failed to load.", loading: false }),
      );
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { ...state, reload: () => setNonce((n) => n + 1) };
}
