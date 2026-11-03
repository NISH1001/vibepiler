import { BaseService, Repository } from './base';

export class UserService extends BaseService {
  private cache = new Map<string, unknown>();

  async find(id: string) {
    this.log(`find ${id}`);
    return this.lookup(id);
  }

  private lookup(id: string) {
    try {
      return this.cache.get(id);
    } catch (e) {
      // ignore
    }
  }

  save(repo: Repository, id: string, value: unknown) {
    try {
      this.cache.set(id, value ?? repo.get(id));
    } catch (e) {
      throw e;
    }
  }
}
