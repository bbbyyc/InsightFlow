import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function proxy(request: NextRequest, context: { params: { path: string[] } }) {
  const apiTarget = process.env.API_PROXY_TARGET || "http://backend:8000";
  const incomingUrl = new URL(request.url);
  const targetUrl = new URL(`/api/${context.params.path.join("/")}`, apiTarget);
  targetUrl.search = incomingUrl.search;

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");
  headers.delete("connection");

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const upstream = await fetch(targetUrl, {
    method: request.method,
    headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
    cache: "no-store",
    redirect: "manual",
  });

  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.delete("content-length");
  responseHeaders.delete("content-encoding");
  responseHeaders.set("Cache-Control", "no-cache, no-transform");
  if (upstream.headers.get("content-type")?.includes("text/event-stream")) {
    responseHeaders.set("X-Accel-Buffering", "no");
  }

  // Passing the upstream ReadableStream directly is essential for SSE. A
  // rewrite may buffer the whole response and make token streaming appear as
  // one final block in the browser.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
