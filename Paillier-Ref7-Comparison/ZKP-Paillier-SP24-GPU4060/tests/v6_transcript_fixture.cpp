// Fixed-randomness compatibility fixture. Works with both original v6 and this project.
// This tiny deterministic key is for regression testing only.
#include "app/CBatchEnc.hpp"
#include <fstream>
void require(bool v,const std::string &s) { if(!v) throw std::runtime_error(s); }
std::string transcript() {
  // Fixed seed is confined to tests. The benchmark never installs it.
  auto crypto=std::make_shared<PaillierEncryption>(8,binary_t{2,7,1,8,2,8});
  auto q=crypto->getGroupQ(),p=crypto->getGroupP();
  auto circuit=std::make_shared<CBatchEnc>(crypto,3,3,4,15);
  auto gi=crypto->genGenerators(circuit->estimateGeneratorsRequired());
  Vec<ZZ> messages; messages.SetLength(3);
  messages[0]=0; messages[1]=1; messages[2]=ZZ(1)<<32;
  circuit->encrypt(messages);
  auto Cm=circuit->Cm,Cm_=circuit->Cm_,CRj=circuit->CRj;
  auto ljir=circuit->calculateLjir(); auto lj=circuit->calculateLj(ljir);
  circuit->wireUp(ljir,lj); circuit->run(ljir,lj);
  auto prover=circuit->generateProverAndRelease(gi);
  Vec<ZZ_p> commits,pc,proof;
  prover->commit(commits); prover->zkp->setCommits(commits);
  auto y=prover->zkp->calculateY(); prover->polyCommit(y,pc);
  prover->zkp->setPolyCommits(pc); auto x=prover->zkp->calculateX(); prover->prove(y,x,proof);
  std::ostringstream out;
  out<<Cm<<Cm_<<CRj<<lj<<commits<<pc<<proof<<y<<x;
  auto vc=std::make_shared<CBatchEnc>(crypto,3,3,4,15);
  vc->setCipher(Cm,Cm_,CRj); auto vljir=vc->calculateLjir();
  require(ljir==vljir,"ciphertext challenge mismatch");
  vc->wireUp(vljir,lj); auto verifier=vc->generateVerifierAndRelease(gi);
  verifier->setCommits(commits); verifier->setPolyCommits(pc);
  auto vy=verifier->calculateY(),vx=verifier->calculateX();
  require(y==vy && x==vx,"transcript challenges mismatch");
  require(verifier->verify(proof,vy,vx),"full proof verification failed");
  { ZZ_pPush push(p); proof[0]+=1; }
  require(!verifier->verify(proof,vy,vx),"tampered proof was accepted");
  return out.str();
}
int main(int argc,char **argv) {
 if(argc!=2) return 2;
 std::ofstream output(argv[1]);
 output<<transcript();
 return output?0:1;
}
