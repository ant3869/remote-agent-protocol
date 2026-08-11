export class SceneLoadGuard {
  constructor(key) {
    this.key = key;
    this.generation = 0;
  }

  updateKey(key) {
    if (key === this.key) return false;
    this.key = key;
    this.generation += 1;
    return true;
  }

  invalidate() {
    this.generation += 1;
  }

  token() {
    return { key: this.key, generation: this.generation };
  }

  accepts(token) {
    return token.key === this.key && token.generation === this.generation;
  }
}
