#include "Util.h"
#include "Config.h"

int main(int argc, char** argv)
{
	Config cfg = Config::FromArgs(argc, argv);

	cfg.Print();
	
}
