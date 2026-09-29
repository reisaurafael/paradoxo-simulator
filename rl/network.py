"""
rl/network.py
=============
A small neural network with its forward and backward pass written in numpy.

No deep learning framework: the layers, the gradients and the Adam optimiser
are all here, so every number the player learns can be traced by hand.

    MLP   fully connected layers with tanh between them and a linear output
    Adam  the optimiser (Kingma and Ba, 2015)
"""

from __future__ import annotations
import numpy as np


class MLP:
    """Fully connected network: sizes[0] inputs, tanh hidden layers, sizes[-1] outputs."""

    def __init__(self, sizes: list[int], rng: np.random.Generator | None = None):
        rng = rng or np.random.default_rng(0)
        self.sizes = list(sizes)
        self.params: list[np.ndarray] = []
        for n_in, n_out in zip(sizes[:-1], sizes[1:]):
            # Xavier initialisation keeps tanh out of saturation at the start.
            limit = np.sqrt(6 / (n_in + n_out))
            self.params.append(rng.uniform(-limit, limit, size=(n_in, n_out)))
            self.params.append(np.zeros(n_out))
        # The output layer starts small, so the first policy is close to uniform.
        self.params[-2] *= 0.1

    def forward(self, x: np.ndarray) -> tuple[np.ndarray, list[np.ndarray]]:
        """Return the output and the activations the backward pass needs."""
        acts = [x]
        h = x
        n_layers = len(self.params) // 2
        for i in range(n_layers):
            w, b = self.params[2 * i], self.params[2 * i + 1]
            h = h @ w + b
            if i < n_layers - 1:
                h = np.tanh(h)
            acts.append(h)
        return h, acts

    def __call__(self, x: np.ndarray) -> np.ndarray:
        return self.forward(x)[0]

    def backward(self, acts: list[np.ndarray], grad_out: np.ndarray) -> list[np.ndarray]:
        """Gradients of sum(grad_out * output) with respect to every parameter."""
        grads = [np.zeros_like(p) for p in self.params]
        g = grad_out
        n_layers = len(self.params) // 2
        for i in reversed(range(n_layers)):
            grads[2 * i] = acts[i].T @ g
            grads[2 * i + 1] = g.sum(axis=0)
            if i > 0:
                g = (g @ self.params[2 * i].T) * (1 - acts[i] ** 2)  # tanh'
        return grads

    def get(self) -> list[np.ndarray]:
        return [p.copy() for p in self.params]

    def set(self, params: list[np.ndarray]) -> None:
        self.params = [p.copy() for p in params]


class Adam:
    """Adam optimiser. step() adds the update to the parameters in place (ascent)."""

    def __init__(self, params: list[np.ndarray], lr: float = 1e-3,
                 beta1: float = 0.9, beta2: float = 0.999, eps: float = 1e-8):
        self.lr, self.b1, self.b2, self.eps = lr, beta1, beta2, eps
        self.m = [np.zeros_like(p) for p in params]
        self.v = [np.zeros_like(p) for p in params]
        self.t = 0

    def step(self, params: list[np.ndarray], grads: list[np.ndarray], ascent: bool = True) -> None:
        self.t += 1
        sign = 1.0 if ascent else -1.0
        for p, g, m, v in zip(params, grads, self.m, self.v):
            m *= self.b1
            m += (1 - self.b1) * g
            v *= self.b2
            v += (1 - self.b2) * g * g
            m_hat = m / (1 - self.b1 ** self.t)
            v_hat = v / (1 - self.b2 ** self.t)
            p += sign * self.lr * m_hat / (np.sqrt(v_hat) + self.eps)
