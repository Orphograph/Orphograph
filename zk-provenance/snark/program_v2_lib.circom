pragma circom 2.0.0;

// program_v2.circom — SNARK circuit for Orphograph PROGRAM_V2 execution proof
// (proof_type target: "snark-exec-v1"; see docs/ZK_SNARK_SPIKE.md).
//
// Statement proven:
//   Given PUBLIC  st0        (= SHA256("orpho-prog-v2" || model_id), computed
//                              publicly by the verifier from the model label)
//   there EXIST PRIVATE p_digest (= SHA256(prompt)),
//                       s_digest (= SHA256(seed)),
//                       r        (256-bit commitment randomness)
//   such that
//     stN        == the N-round PROGRAM_V2 chain over (st0, p_digest, s_digest)
//     commitment == SHA256( p_digest || s_digest || st0 || r )
//   with stN and commitment exposed as public outputs.
//
// The verifier then checks OUTSIDE the circuit (cheap, public):
//     receipt.hash_hex == SHA256( "out2:" + hex(stN) )
// which keeps ASCII-hex encoding out of the circuit entirely.
//
// Round transcript (normative — must match zk_provenance.PROGRAM_V2, which is
// spec-locked by test_program_v2_matches_normative_spec):
//     st_i = SHA256( st_{i-1} || p_digest || s_digest || uint32_be(i) )
// Message length per round: 256 + 256 + 256 + 32 = 800 bits.
//
// Commitment message length: 256*4 = 1024 bits.
//
// All bit arrays are big-endian bit order (bit 0 = MSB of byte 0), matching
// circomlib's sha256 component convention.

include "../node_modules/circomlib/circuits/sha256/sha256.circom";

// One PROGRAM_V2 round: st_i = SHA256(st_prev || p || s || uint32_be(i))
template ProgramV2Round(roundIndex) {
    signal input stPrev[256];
    signal input pDigest[256];
    signal input sDigest[256];
    signal output stNext[256];

    component h = Sha256(800);
    for (var b = 0; b < 256; b++) { h.in[b]       <== stPrev[b]; }
    for (var b = 0; b < 256; b++) { h.in[256 + b] <== pDigest[b]; }
    for (var b = 0; b < 256; b++) { h.in[512 + b] <== sDigest[b]; }
    // uint32_be(roundIndex): 32 bits, MSB first. roundIndex is a compile-time
    // constant, so these are constant signals.
    for (var b = 0; b < 32; b++) {
        h.in[768 + b] <== (roundIndex >> (31 - b)) & 1;
    }
    for (var b = 0; b < 256; b++) { stNext[b] <== h.out[b]; }
}

template ProgramV2(nRounds) {
    // PUBLIC input (the verifier recomputes st0 from the model label).
    signal input st0[256];
    // PRIVATE witnesses.
    signal input pDigest[256];
    signal input sDigest[256];
    signal input r[256];
    // PUBLIC outputs.
    signal output stN[256];
    signal output commitment[256];

    // Enforce bit-ness of private inputs (public st0 bits are the verifier's
    // own responsibility, but constrain them too — it is cheap).
    for (var b = 0; b < 256; b++) {
        st0[b] * (st0[b] - 1) === 0;
        pDigest[b] * (pDigest[b] - 1) === 0;
        sDigest[b] * (sDigest[b] - 1) === 0;
        r[b] * (r[b] - 1) === 0;
    }

    // The round chain.
    component rounds[nRounds];
    for (var i = 0; i < nRounds; i++) {
        rounds[i] = ProgramV2Round(i + 1);   // rounds are 1-indexed in the spec
        for (var b = 0; b < 256; b++) {
            rounds[i].stPrev[b] <== (i == 0) ? st0[b] : rounds[i - 1].stNext[b];
            rounds[i].pDigest[b] <== pDigest[b];
            rounds[i].sDigest[b] <== sDigest[b];
        }
    }
    for (var b = 0; b < 256; b++) { stN[b] <== rounds[nRounds - 1].stNext[b]; }

    // commitment = SHA256(p_digest || s_digest || st0 || r)
    component c = Sha256(1024);
    for (var b = 0; b < 256; b++) { c.in[b]        <== pDigest[b]; }
    for (var b = 0; b < 256; b++) { c.in[256 + b]  <== sDigest[b]; }
    for (var b = 0; b < 256; b++) { c.in[512 + b]  <== st0[b]; }
    for (var b = 0; b < 256; b++) { c.in[768 + b]  <== r[b]; }
    for (var b = 0; b < 256; b++) { commitment[b] <== c.out[b]; }
}

