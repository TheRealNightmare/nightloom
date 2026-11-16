import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type Readiness, type RequestOptions } from "./client";
import { READINESS_POLL_MS, useReadiness } from "./useReadiness";

const READY: Readiness = { kind: "ready", checks: {} };
const OFFLINE: Readiness = { kind: "offline", reason: "down" };

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useReadiness", () => {
  it("starts in the checking state and shows the first answer", async () => {
    const fetcher = vi.fn(() => Promise.resolve(READY));
    const { result } = renderHook(() => useReadiness({ fetcher }));
    expect(result.current.readiness).toEqual({ kind: "checking" });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.readiness).toEqual(READY);
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("polls every 15 seconds by default", async () => {
    expect(READINESS_POLL_MS).toBe(15_000);
    const fetcher = vi
      .fn<(options: RequestOptions) => Promise<Readiness>>()
      .mockResolvedValueOnce(READY)
      .mockResolvedValueOnce(OFFLINE);
    const { result } = renderHook(() => useReadiness({ fetcher }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.readiness).toEqual(READY);

    await act(() => vi.advanceTimersByTimeAsync(14_999));
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.readiness).toEqual(OFFLINE);
  });

  it("rechecks immediately on demand and restarts the interval", async () => {
    const fetcher = vi.fn(() => Promise.resolve(READY));
    const { result } = renderHook(() => useReadiness({ fetcher, intervalMs: 1000 }));
    await act(() => vi.advanceTimersByTimeAsync(500));
    act(() => {
      result.current.recheck();
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(999));
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("ignores the answer of a superseded request", async () => {
    let resolveFirst: (value: Readiness) => void = () => {};
    const fetcher = vi
      .fn<(options: RequestOptions) => Promise<Readiness>>()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockResolvedValueOnce(READY);
    const { result } = renderHook(() => useReadiness({ fetcher }));
    act(() => {
      result.current.recheck();
    });
    const firstSignal = fetcher.mock.calls[0]?.[0].signal;
    expect(firstSignal?.aborted).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(0));
    resolveFirst(OFFLINE);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.readiness).toEqual(READY);
  });

  it("shows offline when the fetcher fails unexpectedly, and keeps polling", async () => {
    const fetcher = vi
      .fn<(options: RequestOptions) => Promise<Readiness>>()
      .mockRejectedValueOnce(new Error("bug"))
      .mockResolvedValue(READY);
    const { result } = renderHook(() => useReadiness({ fetcher, intervalMs: 100 }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.readiness.kind).toBe("offline");
    await act(() => vi.advanceTimersByTimeAsync(100));
    expect(result.current.readiness).toEqual(READY);
  });

  it("stays quiet about aborted requests", async () => {
    const fetcher = vi.fn(() => Promise.reject(new ApiError("Request aborted", "aborted")));
    const { result } = renderHook(() => useReadiness({ fetcher, intervalMs: 100 }));
    await act(() => vi.advanceTimersByTimeAsync(500));
    expect(result.current.readiness).toEqual({ kind: "checking" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("aborts the request and stops polling on unmount", async () => {
    const fetcher = vi.fn<(options: RequestOptions) => Promise<Readiness>>(
      () => new Promise(() => {}),
    );
    const { unmount, result } = renderHook(() => useReadiness({ fetcher, intervalMs: 100 }));
    unmount();
    expect(fetcher.mock.calls[0]?.[0].signal?.aborted).toBe(true);
    result.current.recheck(); // no-op after unmount
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetcher).toHaveBeenCalledOnce();
  });
});
