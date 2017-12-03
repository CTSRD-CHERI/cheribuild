pipeline {

  /* Based on https://github.com/jenkinsci/pipeline-examples/blob/master/declarative-examples/jenkinsfile-examples/mavenDocker.groovy
   * Run everything on an existing agent configured with a label 'docker'.
   * This agent will need docker and git
   */
  agent {
    node {
      label 'docker'
    }
  }
  // using the Timestamper plugin we can add timestamps to the console log
  options {
    timestamps()
  }
  stages {
    stage('Test Python 3.5') {
      agent {
        docker {
          // Reuse the workspace on the agent defined at top-level of Pipeline but run inside a container.
          reuseNode true
          image 'python:3.5.0'
        }
      }
      steps {
        sh './cheribuild.py -p __run_everything__ --cheribsd/crossbuild'
      }
    }
  }
  post {
    failure {
      // notify users when the Pipeline fails
      mail to: 'alr48@cl.cam.acuk',
          subject: "Failed Pipeline: ${currentBuild.fullDisplayName}",
          body: "Something is wrong with ${env.BUILD_URL}"
    }
  }
}
