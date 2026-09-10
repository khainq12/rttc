#ifndef FLEETQOX_MATCHEDMRG32K3ARNG_H
#define FLEETQOX_MATCHEDMRG32K3ARNG_H

#include <cstdint>
#include <omnetpp/crng.h>

// Bit-for-bit port of ns-3's MRG32k3a generator
// (ns-3 src/core/model/rng-stream.cc, L'Ecuyer 2001, "combined multiple
// recursive generator"), registered as an OMNeT++ cRNG so a station's
// Contention module here and its Txop in ns-3 can be driven by the exact
// same (seed, stream, substream) MRG32k3a substream.
//
// Stream-index convention (must match external/ns3/fleetqox_trace_replay.cc's
// --matchedBackoffRng / --matchedBackoffRngBase, default base 0):
//   ns-3 side:  stationDevices[i].AssignStreams(base + i)
//               accessPointDevices[j].AssignStreams(base + stationCount + j)
//   INET side:  rngId (this class's `rngId` argument to initialize(), i.e.
//               the physical RNG slot selected via getRNG(rngId)) must equal
//               the same base + i / base + stationCount + j value, so
//               rngId == ns-3's explicit stream argument directly (base
//               defaults to 0 on both sides for exactly this reason).
//
// Select via ini:
//   [Config MatchedWifi]
//   rng-class = "MatchedMrg32k3aRng"
//   num-rngs = <stationCount + accessPointCount>
//   seed-set = ${seed}   # must equal ns-3's --seed=N --run=N (same N)
class MatchedMrg32k3aRng : public omnetpp::cRNG
{
  private:
    double state[6] = {0, 0, 0, 0, 0, 0};

  public:
    MatchedMrg32k3aRng() {}
    virtual ~MatchedMrg32k3aRng() {}

    virtual void initialize(int seedSet, int rngId, int numRngs, int parsimProcId,
            int parsimNumPartitions, omnetpp::cConfiguration *cfg) override;
    virtual void selfTest() override;
    virtual std::string str() const override;

    virtual uint32_t intRand() override;
    virtual uint32_t intRandMax() override;
    virtual uint32_t intRand(uint32_t n) override;
    virtual double doubleRand() override;
    virtual double doubleRandNonz() override;
    virtual double doubleRandIncl1() override;

  private:
    void seedStreams(uint32_t seedNumber, uint64_t stream, uint64_t substream);
    double randU01();
};

#endif
