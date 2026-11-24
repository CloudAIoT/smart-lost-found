export const API_BASE = '/api';

export class ApiError extends Error {
  constructor(status, code) {
    super(code);
    this.status = status;
    this.code = code;
  }
}

export async function api(path, { method = 'GET', body, signal } = {}) {
  signal?.throwIfAborted();
  const init = { method, signal };
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  let res, text;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
    text = await res.text();
  } catch {
    signal?.throwIfAborted();
    throw new ApiError(0, 'NETWORK_ERROR');
  }
  signal?.throwIfAborted();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    let code = `HTTP_${res.status}`;
    if (data?.detail?.code) code = data.detail.code;
    else if (Array.isArray(data?.detail)) code = 'VALIDATION_ERROR';
    throw new ApiError(res.status, code);
  }
  return data;
}
