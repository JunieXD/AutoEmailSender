import { useEffect, useState } from "react";

/** The current time, moving on every 30 seconds so "N 分钟前" and due colours stay current. */
export function useNow(): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  return now;
}
