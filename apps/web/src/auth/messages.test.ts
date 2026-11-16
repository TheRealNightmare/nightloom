import { describe, expect, it } from "vitest";

import {
  InvalidCredentialsError,
  NetworkError,
  RateLimitedError,
  RequestAbortedError,
  RequestTimeoutError,
  UnexpectedResponseError,
  ValidationError,
} from "../api/client";
import { describeError, rateLimitMessage } from "./messages";

describe("rateLimitMessage", () => {
  it.each([
    [1, "1 minute"],
    [60, "1 minute"],
    [61, "2 minutes"],
    [900, "15 minutes"],
  ])("rounds %i seconds up to %s", (seconds, text) => {
    expect(rateLimitMessage(seconds)).toBe(`Too many attempts. Try again in ${text}.`);
  });
});

describe("describeError", () => {
  it.each([
    [new InvalidCredentialsError(), "Invalid email or password."],
    [new RateLimitedError(120), "Too many attempts. Try again in 2 minutes."],
    [
      new ValidationError([
        { field: null, message: "A." },
        { field: "x", message: "B." },
      ]),
      "A. B.",
    ],
    [new NetworkError(), "Cannot reach Nightloom. Check your connection and try again."],
    [new RequestTimeoutError(), "Nightloom did not answer in time. Try again."],
    [new UnexpectedResponseError(503), "Sign-in is temporarily unavailable. Try again shortly."],
    [new UnexpectedResponseError(500), "Something went wrong. Try again in a moment."],
    [new RequestAbortedError(), "Something went wrong. Try again in a moment."],
    [new Error("raw server text"), "Something went wrong. Try again in a moment."],
  ])("describes %s", (error, message) => {
    expect(describeError(error)).toBe(message);
  });

  it("falls back for an empty validation error", () => {
    expect(new ValidationError([]).message).toBe("The request was not valid.");
  });
});
