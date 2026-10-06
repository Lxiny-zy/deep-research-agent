// Test runtime exposes Node Web Crypto; application source targets browser types.
declare module 'node:crypto' {
  export const webcrypto: Crypto
}
