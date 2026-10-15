// Small semantic and boundary checks; never used by the timing benchmark.
#include "CBatchEnc.hpp"
#include "PolynomialCommitment.hpp"

#include <functional>
#include <memory>

static void require(bool condition, const char *message)
{
  if (!condition)
    throw std::runtime_error(message);
}

static void invalid(const std::function<void()> &operation)
{
  try
  {
    operation();
  }
  catch (const std::invalid_argument &)
  {
    return;
  }
  throw std::runtime_error("invalid input was not rejected");
}

static json values(const Vec<ZZ_p> &v)
{
  json ret = json::array();
  for (long i = 0; i < v.length(); ++i)
    ret.push_back(ConvertUtils::toString(v[i]));
  return ret;
}

// All protocol messages are included so the same test can be compiled against
// the uploaded author sources and the compatibility sources for comparison.
static json transcript()
{
  std::weak_ptr<PaillierEncryption> weakCrypto;
  std::weak_ptr<CircuitZKPProver> weakProver;
  std::weak_ptr<CircuitZKPVerifier> weakVerifier;
  std::weak_ptr<PolynomialCommitment> weakCommitment;
  json out;
  {
    const auto seed = ConvertUtils::toBinary("serial-compatibility-transcript-v1");
    auto crypto = std::make_shared<PaillierEncryption>(16, seed);
    weakCrypto = crypto;
    ZZ_p::init(crypto->getGroupP());
    auto circuit = std::make_shared<CBatchEnc>(crypto, 3, 8, 4, 15);
    auto gi = crypto->genGenerators(circuit->estimateGeneratorsRequired());
    Vec<ZZ> messages;
    messages.append(conv<ZZ>(0));
    messages.append(conv<ZZ>(1));
    messages.append(conv<ZZ>(1) << 32);
    circuit->encrypt(messages);
    auto challenge = circuit->calculateLjir();
    auto response = circuit->calculateLj(challenge);
    circuit->wireUp(challenge, response);
    circuit->run(challenge, response);

    out["N"] = ConvertUtils::toString(crypto->n);
    out["Q"] = ConvertUtils::toString(crypto->Q);
    out["G"] = ConvertUtils::toString(crypto->G);
    out["gates"] = circuit->gateCount;
    out["constraints"] = circuit->linearCount;
    out["ciphertexts"] = values(circuit->Cm);
    out["auxiliary_ciphertexts"] = values(circuit->Cm_);
    out["range_ciphertexts"] = values(circuit->CRj);
    out["challenge"] = ConvertUtils::toString(ConvertUtils::toZZ(challenge));
    out["response"] = values(response);
    out["generators"] = values(gi);
    out["Kq"] = values(circuit->Kq);

    Vec<ZZ_p> cipher = circuit->Cm;
    Vec<ZZ_p> auxiliary = circuit->Cm_;
    Vec<ZZ_p> range = circuit->CRj;
    auto prover = circuit->generateProver(gi);
    weakProver = prover;
    weakVerifier = prover->zkp;
    weakCommitment = prover->zkp->commitScheme;
    circuit.reset();

    Vec<ZZ_p> commits, pc, proofs;
    prover->commit(commits);
    prover->zkp->setCommits(commits);
    auto y = prover->zkp->calculateY();
    prover->polyCommit(y, pc);
    prover->zkp->setPolyCommits(pc);
    auto x = prover->zkp->calculateX();
    prover->prove(y, x, proofs);
    out["commits"] = values(commits);
    out["pc"] = values(pc);
    out["proofs"] = values(proofs);
    out["y"] = ConvertUtils::toString(y);
    out["x"] = ConvertUtils::toString(x);
    prover.reset();
    require(weakProver.expired() && weakVerifier.expired() && weakCommitment.expired(),
            "proof objects retain a shared ownership cycle");

    auto pk = crypto->getPublicKey();
    auto groupQ = crypto->getGroupQ();
    auto groupP = crypto->getGroupP();
    auto groupG = crypto->getGroupG();
    auto encryptor = std::make_shared<PaillierEncryption>(pk, groupQ, groupP, groupG);
    auto verifierCircuit = std::make_shared<CBatchEnc>(encryptor, 3, 8, 4, 15);
    verifierCircuit->setCipher(cipher, auxiliary, range);
    auto verifierChallenge = verifierCircuit->calculateLjir();
    require(ConvertUtils::eq(challenge, verifierChallenge), "cipher challenge mismatch");
    verifierCircuit->wireUp(verifierChallenge, response);
    auto verifier = verifierCircuit->generateVerifier(gi);
    verifierCircuit.reset();
    verifier->setCommits(commits);
    auto verifierY = verifier->calculateY();
    verifier->setPolyCommits(pc);
    auto verifierX = verifier->calculateX();
    require(y == verifierY && x == verifierX, "Fiat-Shamir challenge mismatch");
    require(verifier->verify(proofs, verifierY, verifierX), "valid proof was rejected");

    {
      ZZ_pPush push(groupP);
      proofs[0] += 1;
    }
    require(!verifier->verify(proofs, verifierY, verifierX), "modified proof was accepted");
    require(crypto->decrypt(cipher[1]) == messages[1], "Paillier decrypt mismatch");
  }
  require(weakCrypto.expired(), "crypto object was retained after its scope");
  return out;
}

static void boundaries()
{
  ZZ_p::init(conv<ZZ>(101));
  auto first = std::make_shared<Matrix>(1, 12);
  auto second = std::make_shared<Matrix>(1, 12);
  first->cell(0, 11, 1);
  second->cell(0, 11, 2);
  require(!first->eq(second), "sparse matrix equality ignores high columns");
  require(first->eq(first->clone()), "matrix clone mismatch");
  invalid([&] { first->group(0); });

  auto crypto = std::make_shared<PaillierEncryption>(16, ConvertUtils::toBinary("serial-boundary-test-v1"));
  ZZ_p::init(crypto->getGroupP());
  auto circuit = std::make_shared<CBatchEnc>(crypto, 1, 8, 4, 15);
  Vec<ZZ> message;
  message.append(conv<ZZ>(0));
  circuit->encrypt(message);
  invalid([&] { circuit->calculateLj(binary_t()); });
  Vec<ZZ_p> response;
  invalid([&] { circuit->wireUp(binary_t(), response); });

  // Reconstruct the exact deterministic integer to verify explicit zero padding.
  // Vary only the ciphertext representation to find a challenge with a zero
  // high byte; this regression hit the author's out-of-bounds access.
  bool sawZeroHighByte = false;
  for (long trial = 0; trial < 4096 && !sawZeroHighByte; ++trial)
  {
    circuit->Cm[0] = conv<ZZ_p>(trial);
    binary_t seed;
    for (auto c : circuit->Cm)
      ConvertUtils::append(seed, ConvertUtils::toBinary(c));
    for (auto c : circuit->Cm_)
      ConvertUtils::append(seed, ConvertUtils::toBinary(c));
    for (auto c : circuit->CRj)
      ConvertUtils::append(seed, ConvertUtils::toBinary(c));
    SetSeed(seed.data(), seed.size());
    auto expected = RandomBits_ZZ(circuit->getLjLength() * 8);
    auto bytes = circuit->calculateLjir();
    require(bytes.size() == circuit->getLjLength(), "challenge is not fixed size");
    require(ConvertUtils::toZZ(bytes) == expected, "challenge padding changes the integer");
    sawZeroHighByte = ConvertUtils::toBinary(expected).size() < bytes.size();
  }
  require(sawZeroHighByte, "zero-high-byte challenge regression was not exercised");

  auto gi = crypto->genGenerators(8);
  PolynomialCommitment scheme(crypto, gi);
  // Compare the original serial bucket implementation with the defining group
  // product, including 8-bit digit boundaries and empty/zero messages.
  for (long len = 0; len <= 8; ++len)
  {
    ZZ_p::init(crypto->getGroupP());
    Vec<ZZ_p> ms;
    ms.SetLength(len);
    for (long i = 0; i < len; ++i)
      ms[i] = conv<ZZ_p>((conv<ZZ>(1) << (i * 9)) + (i * 255));
    auto randomness = conv<ZZ_p>(257);
    auto originalModulus = ZZ_p::modulus();
    auto bucketResult = scheme.commit(ms, randomness);
    require(ZZ_p::modulus() == originalModulus, "commit failed to restore the NTL modulus");
    {
      ZZ_pPush push(crypto->getGroupQ());
      auto reference = power(crypto->G, conv<ZZ>(randomness));
      for (long i = 0; i < len; ++i)
        reference *= power(gi[i], conv<ZZ>(ms[i]));
      require(bucketResult == reference, "bucket commitment differs from group product");
    }
  }
  Vec<ZZ_p> tooLong;
  tooLong.SetLength(9);
  invalid([&] { scheme.commit(tooLong, ZZ_p()); });
  Mat<ZZ_p> matrix;
  matrix.SetDims(2, 2);
  Vec<ZZ_p> wrongRandomness, ignored;
  wrongRandomness.SetLength(1);
  invalid([&] { scheme.commit(matrix, wrongRandomness, ignored); });
}

int main(int argc, char **argv)
{
  try
  {
    // Suppress instrumentation text, whose values are timing-dependent.
    std::ostringstream trace;
    auto *prior = std::cout.rdbuf(trace.rdbuf());
    json snapshot;
    try
    {
      snapshot = transcript();
      if (!(argc == 2 && std::string(argv[1]) == "--snapshot"))
        boundaries();
    }
    catch (...)
    {
      std::cout.rdbuf(prior);
      throw;
    }
    std::cout.rdbuf(prior);
    if (argc == 2 && std::string(argv[1]) == "--snapshot")
      std::cout << snapshot.dump() << '\n';
    else
      std::cout << "serial core semantic and boundary checks: PASS\n";
    return 0;
  }
  catch (const std::exception &error)
  {
    std::cerr << "serial core check: FAIL: " << error.what() << '\n';
    return 1;
  }
}
