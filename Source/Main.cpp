#include "Util.h"
#include "Config.h"
#include "NetInterface.h"
#include "NCSClient.h"

int main(int argc, char** argv)
{
	(void)NetInterface::InitNet();

	Config cfg = Config::FromArgs(argc, argv);

	cfg.Print();
	
}
