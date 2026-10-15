"""
Decoder for compressed profile traces, ported from the reference C the vendor supplies. Imports nothing from the
rest of the project so it can be tested on its own.
"""

WORD_MASK = 0xFFFF
WORD_RANGE = 0x10000

# the leading word of each block says what kind it is. read as a signed number, negative is a run, a value from
# DELTA_BLOCK up is a delta block, and anything smaller is a count of plain words
NEGATIVE_FROM = 0x8000
DELTA_BLOCK = 0x4000

# each delta word holds three signed deltas, most significant first
DELTAS_PER_WORD = 3
DELTA_BITS = 5
DELTA_SHIFT = 10
DELTA_WORD_MASK = 0x7FFF
DELTA_MAX = 15
DELTA_RANGE = 32


def to_signed(word):
    """
    A 16 bit word reinterpreted as a signed number.
    """
    if word >= NEGATIVE_FROM:
        return word - WORD_RANGE
    return word


def next_word(encoded, pos):
    """
    The word at pos, raising instead of reading past the end like the C would.
    """
    if pos >= len(encoded):
        raise ValueError(f"encoded data ends at word {len(encoded)} in the middle of a block")
    return encoded[pos]


def decode(encoded, max_samples):
    """
    Expand a list of encoded words into at most max_samples unsigned samples.
    """
    samples = []
    pos = 0

    while pos < len(encoded):
        lead = encoded[pos]
        pos += 1

        if lead >= NEGATIVE_FROM:
            # a run, that many copies of the next word
            count = WORD_RANGE - lead
            value = next_word(encoded, pos)
            pos += 1
            # stop rather than run away, too many samples means the data isn't what we think it is
            if len(samples) + count > max_samples:
                break
            samples.extend([value] * count)

        elif lead >= DELTA_BLOCK:
            # a starting value, then each sample is the one before minus a small delta
            count = lead - DELTA_BLOCK
            value = next_word(encoded, pos)
            pos += 1
            if len(samples) >= max_samples:
                break
            samples.append(value)

            while count > 0:
                packed = next_word(encoded, pos)
                pos += 1
                count -= DELTAS_PER_WORD
                for _ in range(DELTAS_PER_WORD):
                    # the deltas sit under the top bit, which should always be clear. the reference doesn't mask it
                    # off before the first shift and neither does this, so a set top bit decodes the same as in C
                    delta = packed >> DELTA_SHIFT
                    if delta > DELTA_MAX:
                        delta -= DELTA_RANGE
                    packed = (packed << DELTA_BITS) & DELTA_WORD_MASK

                    # C truncates to sixteen bits on its own, Python has to be told
                    value = (value - delta) & WORD_MASK

                    # the count isn't always a whole number of words, so the last word of a block can hold more
                    # deltas than there are samples left. this is what stops it
                    if len(samples) >= max_samples:
                        break
                    samples.append(value)

        else:
            # that many plain words. the reference counts this branch in bytes while every other branch counts
            # in words, which would overrun the output and skip input, so it is counted in words here too
            count = lead
            if len(samples) + count > max_samples:
                break
            if pos + count > len(encoded):
                raise ValueError(f"encoded data ends at word {len(encoded)} in the middle of a block")
            samples.extend(encoded[pos:pos + count])
            pos += count

    return samples
