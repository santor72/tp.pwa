export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message)
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    headers: { Accept: 'application/json', ...init.headers },
    ...init,
  })
  if (response.status === 204) return undefined as T
  const data = await response.json().catch(() => ({}))
  if (response.status === 401 && path !== '/api/auth/login') {
    window.dispatchEvent(new Event('auth-expired'))
  }
  if (!response.ok) {
    if (response.status === 413) {
      throw new ApiError(413, data.code ?? 'REQUEST_TOO_LARGE', data.message ?? 'Размер вложений превышает допустимый для отправки. Уменьшите размер или количество фотографий.')
    }
    throw new ApiError(response.status, data.code ?? 'REQUEST_FAILED', data.message ?? 'Ошибка запроса')
  }
  return data as T
}
