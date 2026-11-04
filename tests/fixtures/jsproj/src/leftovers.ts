// Leftovers a coding agent leaves inside function bodies: the liveness pass's fixture. Each function is one case.
// (`handler` would be an entry point and `cache` is read in services/user.ts, so neither name is used here.)

export function neverRead(): number {
  const items = []
  return 0
}

export function overwritten(): number[] {
  let data: number[] = []
  data = load()
  return data
}

export function callKept(): number {
  const result = compute()
  return 0
}

export function notify(message: string, channel: string): string {
  return message
}

export function callback(err: Error, value: string): string {
  return value
}

export class Service {
  constructor() {
    this.lastSeen = {}
    this.name = "svc"
  }

  describe(): string {
    return this.name
  }
}
