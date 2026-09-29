"""
rl/
===
A Paradoxo player that learns by reinforcement.

The four heuristic players in simulation/strategies/ follow rules written by
hand. This package trains a player that writes its own rules for the most
important decision of the game, where the four generators go every Hour,
by playing thousands of matches against the heuristic players and keeping
what wins.

    actions.py   every legal move of one Hour, listed one by one
    features.py  what the network sees: the table, and each move's outcome
    network.py   a small neural network, forward and backward pass in numpy
    agent.py     the learned player, a Strategy like any other
    train.py     policy gradient training (REINFORCE with a value baseline)
    evaluate.py  the benchmark: the same table, one seat swapped
"""
