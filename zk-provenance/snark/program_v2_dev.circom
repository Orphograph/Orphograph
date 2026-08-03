pragma circom 2.0.0;

// DEV PROFILE ONLY - 2-round variant used to validate the toolchain
// (compile -> setup -> prove -> verify) with a small local powers-of-tau.
// NOT the normative circuit: PROGRAM_V2 is 8 rounds (spec-locked).
// Never ship proofs from this profile.

include "program_v2_lib.circom";

component main {public [st0]} = ProgramV2(2);
