"""The four stages. Each one decides only which examples the model sees; everything else is shared (common/)."""

from stages import stage1_zero_shot, stage2_retrieval, stage3_contamination, stage4_attacks

STAGES = {
    1: stage1_zero_shot,  # no examples
    2: stage2_retrieval,  # the 3 most similar clean examples
    3: stage3_contamination,  # same retrieval, part of the memory pool is wrong
    4: stage4_attacks,  # same retrieval, then a worst-case attack on what is shown
}
