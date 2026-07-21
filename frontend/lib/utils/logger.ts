/**
 * Frontend logging utility.
 *
 * In production (NODE_ENV === 'production'), all console calls are suppressed
 * to prevent leaking internal state, error messages, or stack traces to the
 * browser console.
 *
 * 审计 P2：25+ console.error / console.warn 语句在生产环境泄漏内部状态。
 * 使用此模块替代直接 console.* 调用。
 */

const IS_DEV = process.env.NODE_ENV === "development";

export const devOnly = {
  log: (...args: unknown[]) => {
    if (IS_DEV) console.log(...args);
  },
  warn: (...args: unknown[]) => {
    if (IS_DEV) console.warn(...args);
  },
  error: (...args: unknown[]) => {
    if (IS_DEV) console.error(...args);
  },
};

/**
 * Always log errors (even in production) but sanitize the message.
 * Use this for user-facing errors where the user should see something.
 */
export const safeError = (...args: unknown[]) => {
  if (IS_DEV) {
    console.error(...args);
  } else {
    // Production: log generic message without internal details
    console.error("An error occurred. Please try again or contact support.");
  }
};
