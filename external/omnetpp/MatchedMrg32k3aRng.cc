//
// Bit-for-bit port of ns-3's MRG32k3a implementation
// (ns-3 src/core/model/rng-stream.cc). The constants, matrices and
// algorithm below are copied verbatim from that file (itself derived from
// Pierre L'Ecuyer's RngStream reference implementation) so that this class
// reproduces the exact same output sequence as ns-3's ns3::RngStream for a
// given (seed, stream, substream) triple. See MatchedMrg32k3aRng.h for the
// stream-index convention that keeps the two simulators' indices aligned.
//

#include "MatchedMrg32k3aRng.h"

#include <omnetpp/cconfiguration.h>
#include <omnetpp/cexception.h>

using namespace omnetpp;

namespace {

typedef double Matrix[3][3];

const double m1 = 4294967087.0;
const double m2 = 4294944443.0;
const double norm = 1.0 / (m1 + 1.0);
const double a12 = 1403580.0;
const double a13n = 810728.0;
const double a21 = 527612.0;
const double a23n = 1370589.0;
const double two17 = 131072.0;
const double two53 = 9007199254740992.0;

const Matrix A1p0 = {
    {0.0, 1.0, 0.0},
    {0.0, 0.0, 1.0},
    {-810728.0, 1403580.0, 0.0}
};

const Matrix A2p0 = {
    {0.0, 1.0, 0.0},
    {0.0, 0.0, 1.0},
    {-1370589.0, 0.0, 527612.0}
};

double MultModM(double a, double s, double c, double m)
{
    double v;
    int32_t a1;

    v = a * s + c;

    if (v >= two53 || v <= -two53) {
        a1 = static_cast<int32_t>(a / two17);
        a -= a1 * two17;
        v = a1 * s;
        a1 = static_cast<int32_t>(v / m);
        v -= a1 * m;
        v = v * two17 + a * s + c;
    }

    a1 = static_cast<int32_t>(v / m);
    if ((v -= a1 * m) < 0.0)
        return v += m;
    else
        return v;
}

void MatVecModM(const Matrix A, const double s[3], double v[3], double m)
{
    double x[3];
    for (int i = 0; i < 3; ++i) {
        x[i] = MultModM(A[i][0], s[0], 0.0, m);
        x[i] = MultModM(A[i][1], s[1], x[i], m);
        x[i] = MultModM(A[i][2], s[2], x[i], m);
    }
    for (int i = 0; i < 3; ++i)
        v[i] = x[i];
}

void MatMatModM(const Matrix A, const Matrix B, Matrix C, double m)
{
    double V[3];
    Matrix W;
    for (int i = 0; i < 3; ++i) {
        for (int j = 0; j < 3; ++j)
            V[j] = B[j][i];
        MatVecModM(A, V, V, m);
        for (int j = 0; j < 3; ++j)
            W[j][i] = V[j];
    }
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j)
            C[i][j] = W[i][j];
}

void MatTwoPowModM(const Matrix src, Matrix dst, double m, int32_t e)
{
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j)
            dst[i][j] = src[i][j];
    for (int i = 0; i < e; i++)
        MatMatModM(dst, dst, dst, m);
}

struct Precalculated
{
    Matrix a1[190];
    Matrix a2[190];
};

Precalculated PowerOfTwoConstants()
{
    Precalculated precalculated;
    for (int i = 0; i < 190; i++) {
        int power = i + 1;
        MatTwoPowModM(A1p0, precalculated.a1[i], m1, power);
        MatTwoPowModM(A2p0, precalculated.a2[i], m2, power);
    }
    return precalculated;
}

void PowerOfTwoMatrix(int n, Matrix a1p, Matrix a2p)
{
    static Precalculated constants = PowerOfTwoConstants();
    for (int i = 0; i < 3; i++) {
        for (int j = 0; j < 3; j++) {
            a1p[i][j] = constants.a1[n - 1][i][j];
            a2p[i][j] = constants.a2[n - 1][i][j];
        }
    }
}

void AdvanceNthBy(uint64_t nth, int by, double state[6])
{
    Matrix matrix1;
    Matrix matrix2;
    for (int i = 0; i < 64; i++) {
        int nbit = 63 - i;
        int bit = (nth >> nbit) & 0x1;
        if (bit) {
            PowerOfTwoMatrix(by + nbit, matrix1, matrix2);
            MatVecModM(matrix1, state, state, m1);
            MatVecModM(matrix2, &state[3], &state[3], m2);
        }
    }
}

} // namespace

Register_Class(MatchedMrg32k3aRng);

void MatchedMrg32k3aRng::seedStreams(uint32_t seedNumber, uint64_t stream, uint64_t substream)
{
    if (seedNumber >= m1 || seedNumber >= m2 || seedNumber == 0)
        throw cRuntimeError("MatchedMrg32k3aRng: invalid seed %u (must be in [1, %.0f))",
                seedNumber, m1 < m2 ? m1 : m2);
    for (int i = 0; i < 6; ++i)
        state[i] = seedNumber;
    AdvanceNthBy(stream, 127, state);
    AdvanceNthBy(substream, 76, state);
}

double MatchedMrg32k3aRng::randU01()
{
    int32_t k;
    double p1;
    double p2;
    double u;

    p1 = a12 * state[1] - a13n * state[0];
    k = static_cast<int32_t>(p1 / m1);
    p1 -= k * m1;
    if (p1 < 0.0)
        p1 += m1;
    state[0] = state[1];
    state[1] = state[2];
    state[2] = p1;

    p2 = a21 * state[5] - a23n * state[3];
    k = static_cast<int32_t>(p2 / m2);
    p2 -= k * m2;
    if (p2 < 0.0)
        p2 += m2;
    state[3] = state[4];
    state[4] = state[5];
    state[5] = p2;

    u = (p1 > p2) ? (p1 - p2) * norm : (p1 - p2 + m1) * norm;
    return u;
}

void MatchedMrg32k3aRng::initialize(int seedSet, int rngId, int numRngs, int /*parsimProcId*/,
        int /*parsimNumPartitions*/, cConfiguration * /*cfg*/)
{
    // seedSet doubles as both ns-3's RngSeed and RngRun (the FleetQoX
    // wifi-parity matched-RNG convention always invokes both simulators
    // with --seed=N --run=N / seed-set=N for the same N), and rngId (the
    // physical RNG slot chosen via getRNG(rngId)) must equal the ns-3
    // AssignStreams() argument for the corresponding station/AP -- see the
    // header comment for the exact convention.
    if (seedSet <= 0)
        throw cRuntimeError("MatchedMrg32k3aRng: seed-set must be a positive integer "
                "(ns-3's RngSeedManager rejects seed 0)");
    if (rngId < 0)
        throw cRuntimeError("MatchedMrg32k3aRng: rngId must be >= 0");
    uint32_t seedNumber = static_cast<uint32_t>(seedSet);
    uint64_t stream = (1ULL << 63) + static_cast<uint64_t>(rngId);
    uint64_t substream = static_cast<uint64_t>(seedSet);
    seedStreams(seedNumber, stream, substream);
}

void MatchedMrg32k3aRng::selfTest()
{
    // Structural smoke test only (OMNeT++ calls this once at startup and
    // expects an exception on failure). Exact cross-simulator agreement
    // with ns-3 is verified empirically via the wifi-parity diagnostic
    // harness, not here.
    seedStreams(12345, 0, 0);
    for (int i = 0; i < 10000; i++) {
        double u = randU01();
        if (!(u >= 0.0 && u < 1.0))
            throw cRuntimeError("MatchedMrg32k3aRng: selfTest() failed, "
                    "RandU01() produced out-of-range value %g", u);
    }
}

std::string MatchedMrg32k3aRng::str() const
{
    return "MatchedMrg32k3aRng(numDrawn=" + std::to_string(numDrawn) + ")";
}

uint32_t MatchedMrg32k3aRng::intRand()
{
    numDrawn++;
    return static_cast<uint32_t>(randU01() * 4294967296.0);
}

uint32_t MatchedMrg32k3aRng::intRandMax()
{
    return 0xFFFFFFFFu;
}

uint32_t MatchedMrg32k3aRng::intRand(uint32_t n)
{
    // Mirrors ns-3's UniformRandomVariable::GetInteger(0, n-1):
    //   static_cast<uint32_t>(RandU01() * ((n - 1) - 0 + 1))
    //   == static_cast<uint32_t>(RandU01() * n)
    // which is exactly what Txop::GetBackoffSlots() computes via
    // m_rng->GetInteger(0, cw) with n = cw + 1.
    numDrawn++;
    return static_cast<uint32_t>(randU01() * static_cast<double>(n));
}

double MatchedMrg32k3aRng::doubleRand()
{
    numDrawn++;
    return randU01();
}

double MatchedMrg32k3aRng::doubleRandNonz()
{
    double v;
    do {
        v = randU01();
    } while (v == 0.0);
    numDrawn++;
    return v;
}

double MatchedMrg32k3aRng::doubleRandIncl1()
{
    numDrawn++;
    return randU01() * (m1 / (m1 - 1.0));
}
