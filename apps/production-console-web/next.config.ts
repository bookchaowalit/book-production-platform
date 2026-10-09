import type { NextConfig } from 'next';

const config: NextConfig = { reactStrictMode: true, turbopack: { root: process.cwd() } };

export default config;
