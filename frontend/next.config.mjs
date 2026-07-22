/** @type {import('next').NextConfig} */
const API_GATEWAY = process.env.API_GATEWAY_URL || 'http://localhost:8000';
// control-api (control plane: auth + /trading/* writes). Separate service from
// the read-only api-gateway. Defaults to its local-dev host port (8001).
const CONTROL_API = process.env.CONTROL_API_URL || 'http://localhost:8001';

const nextConfig = {
  reactStrictMode: true,
  // Lint is run separately (`npm run lint`); don't fail production builds on it.
  eslint: { ignoreDuringBuilds: true },
  modularizeImports: {
    '@mui/icons-material': {
      transform: '@mui/icons-material/{{member}}',
    },
  },
  // Proxy REST calls to the FastAPI api-gateway so the browser talks to a
  // same-origin `/api/gateway/*` path (avoids CORS, keeps the JWT on one host).
  async rewrites() {
    return [
      {
        source: '/api/gateway/:path*',
        destination: `${API_GATEWAY}/:path*`,
      },
      // Control plane -> control-api (auth + /trading/* command endpoints).
      {
        source: '/api/control/:path*',
        destination: `${CONTROL_API}/:path*`,
      },
    ];
  },
};

export default nextConfig;
