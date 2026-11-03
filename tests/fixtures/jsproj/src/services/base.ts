export abstract class BaseService {
  abstract find(id: string): Promise<unknown>;

  protected log(msg: string) {
    console.log(msg);
  }
}

export interface Repository {
  get(id: string): unknown;
}
